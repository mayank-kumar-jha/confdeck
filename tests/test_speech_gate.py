import builtins
import json
import math
import subprocess
import sys

import numpy as np
import pytest

from confdeck.evaluation.speech import compare, merge_scalar_frames, run
from confdeck.evaluation.synthetic import generate
from confdeck.speech import (
    GateConfig,
    SileroFrames,
    SpeechGate,
    estimate_sync,
    passes_guard,
    suggest_calibration,
)
from confdeck.tracking_math.engine import SphericalFilter
from confdeck.tracking_math.quality import QualityGate


def frames(gate, probability, level=-60, count=2, start=0.):
    return [gate.push(start+i*.032, level, probability) for i in range(count)]


def test_onset_hangover_and_no_evidence_during_release():
    gate = SpeechGate()
    result = frames(gate, .9)
    assert result[0]['reliability'] == 0
    assert result[1]['transition'] == 'speech_start'
    assert result[1]['reliability'] > 0
    release = frames(gate, .1, count=11, start=.064)
    assert release[0]['active'] and release[0]['reliability'] == 0
    assert release[-1]['transition'] == 'speech_end'


@pytest.mark.parametrize('level,p', [(-80, .99), (-70, .99), (-30, .1), (-60, .5)])
def test_dual_gate_truth_table(level, p):
    assert frames(SpeechGate(), p, level)[-1]['reliability'] == 0


def test_quiet_weight_and_low_voice_sustained():
    quiet = frames(SpeechGate(), .8, -68)[-1]['reliability']
    normal = frames(SpeechGate(), .9, -45)[-1]['reliability']
    assert 0 < quiet < normal <= 1
    gate = SpeechGate(GateConfig(low_voice=True))
    result = frames(gate, .45, -68, count=8)
    assert all(x['reliability'] == 0 for x in result[:5])
    assert result[-1]['reliability'] > 0
    assert frames(SpeechGate(GateConfig(low_voice=True)), .45, -40, count=8)[-1]['reliability'] == 0


def test_gap_stale_future_and_invalid_frames():
    gate = SpeechGate()
    frames(gate, .9)
    assert gate.evidence(-1) is None
    assert gate.evidence(.2) is None
    assert gate.push(1., -60, .9)['reliability'] == 0
    with pytest.raises(ValueError):
        gate.push(1., -60, .9)
    for value in (math.nan, math.inf, True):
        with pytest.raises(ValueError):
            gate.push(2., -60, value)


def test_far_end_and_audio_loss_preserve_old_gate():
    _, settings = generate()
    speech, fallback = QualityGate(settings, speech=True), QualityGate(settings)
    for n in range(10):
        event = dict(azimuth=20+n*.01, elevation=20, level=-35)
        assert speech.feed(event, n*.1)[:2] == fallback.feed(event, n*.1)[:2]
    event = dict(azimuth=315, elevation=90, level=-35, speech={'reliability': 1})
    weight, reason, _ = speech.feed(event, 2.)
    assert weight == 0 and 'far-end' in reason
    event.update(azimuth=20, elevation=20, beam_freeze=True)
    assert speech.feed(event, 3.)[0] == 0


def test_preroll_bounded_corroborated_and_cleared_by_far_end():
    _, settings = generate()
    tracker = SphericalFilter(settings, speech=True)
    for n in range(9):
        tracker.feed(dict(azimuth=20+n*.01, elevation=20, level=-65,
                          speech={'reliability': 0}), n*.1)
    assert tracker.pre_roll
    assert all(.8-t <= .4+1e-9 for t, _ in tracker.pre_roll)
    tracker.feed(dict(azimuth=20.1, elevation=20, level=-65,
                      speech={'reliability': .4}), .9)
    assert not tracker.pre_roll
    assert tracker.gate.last_reason == 'accepted'
    tracker.feed(dict(azimuth=20.2, elevation=20, level=-65,
                      speech={'reliability': 0}), 1.)
    assert tracker.pre_roll
    tracker.feed(dict(azimuth=315, elevation=90, level=-35,
                      speech={'reliability': 1}), 1.1)
    assert not tracker.pre_roll


def test_speech_weight_cannot_override_activity_or_invalid_evidence():
    _, settings = generate()
    gate = QualityGate(settings, speech=True)
    event = dict(azimuth=20, elevation=20, level=-65, speech={'reliability': .3})
    assert gate.feed(event, 0.)[0] == pytest.approx(.3)
    assert gate.feed({**event, 'activity': False}, .1)[0] == 0
    assert gate.feed({**event, 'speech': {'reliability': math.nan}}, .2)[0] == 0


def test_sync_and_calibration_do_not_write(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected file access')
    monkeypatch.setattr(builtins, 'open', forbidden)
    assert estimate_sync([1, 2, 3], [1.1, 2.1, 3.1]) == pytest.approx(.1)
    with pytest.raises(ValueError):
        estimate_sync([1, 2, 3], [1.1, 2.5, 3.1])
    phases = dict(silence=[(-75, .05)]*313, normal=[(-40, .95)]*50,
                  quiet=[(-66, .65)]*50, noise=[(-20, .1)]*50)
    result = suggest_calibration(phases)
    assert result['requires_confirmation']
    assert result['suggestion']['quiet_detected']
    assert result['suggestion']['noise_triggers'] == 0
    phases['noise'] = [(-20, .99)]*50
    assert suggest_calibration(phases)['suggestion'] is None


def test_local_model_required_and_silero_context(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match='Missing local'):
        SileroFrames(tmp_path/'absent.onnx')
    # Exercise adapter contract with a fake inference session, not fake acoustic accuracy.
    processor = SileroFrames.__new__(SileroFrames)
    processor.np = np
    processor.reset()
    class Session:
        def run(self, names, inputs):
            assert inputs['input'].shape == (1, 576)
            assert inputs['state'].shape == (2, 1, 128)
            assert inputs['sr'] == 16000
            return np.array([[.8]]), inputs['state']
    processor.session = Session()
    result = processor.process(np.ones(512, dtype=np.float32)*.01)
    assert result['level'] == pytest.approx(-40, abs=.001)
    assert result['probability'] == .8 and not result['clipping']
    assert processor.process(np.zeros(512))['no_signal']
    with pytest.raises(ValueError):
        processor.process(np.zeros(511))


def test_guard_rejects_missing_metrics_and_any_regression():
    before = dict(wrong_rate=.01, false_triggers_per_10min=10, quiet_lock_rate=.5, median_latency=1.)
    after = dict(wrong_rate=.01, false_triggers_per_10min=5, quiet_lock_rate=.8, median_latency=1.1)
    assert passes_guard(before, after)
    for key, value in [('wrong_rate', .011), ('false_triggers_per_10min', 10),
                       ('quiet_lock_rate', .5), ('median_latency', 1.101)]:
        assert not passes_guard(before, {**after, key: value})
        assert not passes_guard(before, {**after, key: None})


def test_baseline_without_audio_identical_and_guard_rejects():
    session, settings = generate()
    result = compare(session, settings)
    assert not result['accepted']
    assert result['before'] == result['after']


def test_scalar_vad_capture_aligns_to_accuracy_session_without_audio(tmp_path):
    session, _ = generate()
    session['monotonic_start'] = 5000.
    path = tmp_path/'vad.jsonl'
    records = [
        dict(kind='speech_frames', version=1, clock='monotonic', started_monotonic=5000.),
        dict(kind='speech_frame', time=5000.032, level=-68., probability=.75),
        dict(kind='speech_frame', time=5000.064, level=-67., probability=.82),
        dict(kind='summary', dropped=0),
    ]
    path.write_text(''.join(json.dumps(row)+'\n' for row in records), encoding='utf-8')
    merged = merge_scalar_frames(session, [path])
    frames_out = [row for row in merged['rows'] if row['kind'] == 'speech_frame']
    assert [row['t'] for row in frames_out] == pytest.approx([.032, .064])
    assert all(set(row) == {'kind', 't', 'level', 'probability'} for row in frames_out)
    records[-1]['dropped'] = 1
    path.write_text(''.join(json.dumps(row)+'\n' for row in records), encoding='utf-8')
    with pytest.raises(ValueError, match='dropped'):
        merge_scalar_frames(session, [path])


def test_disabled_has_no_speech_imports():
    result = subprocess.run([sys.executable, '-c',
        "import sys; from confdeck.evaluation.synthetic import generate; "
        "from confdeck.evaluation.replay import replay; "
        "s,c=generate(); replay(s,c,'new'); "
        "assert not any(m in sys.modules for m in ('confdeck.speech','sounddevice','onnxruntime'))"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_speech_replay_has_full_coverage_and_rejects_noise():
    session, settings = generate()
    end = max(x['end'] for x in session['labels'])
    session['rows'].extend(dict(kind='speech_frame', t=round(i*.032, 6), level=-30., probability=.01)
                           for i in range(int(end/.032)+1))
    session['rows'].sort(key=lambda r: (r['t'], r['kind'] != 'speech_frame'))
    result = run(session, settings, True)
    assert result['audio_coverage'] == 1
    assert result['false_triggers_per_10min'] == 0
    assert result['quiet_lock_rate'] == 0
