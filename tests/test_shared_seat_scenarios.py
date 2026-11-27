"""Scenario checks use simulated scalar observations, not recorded room audio."""
import pytest

from confdeck.evaluation.replay import make_engine
from confdeck.tracking import MicrophoneProfile, TrackingSettings, Zone
from confdeck.tracking_math.quality import QualityGate


def shared_settings(kind='legacy'):
    return TrackingSettings(engine=kind, confirm_ms=100, min_hold_ms=500, settle_ms=0,
        ema_alpha=1, zone_margin_deg=5, inference={'evidence_floor_ms': 100}, zones=[
            Zone(id='a', name='A', azimuth=20, elevation=30, presets={'cam': '1'},
                 microphone_profiles={'rear': MicrophoneProfile(azimuth=210, elevation=30)}),
            Zone(id='b', name='B', azimuth=100, elevation=30, presets={'cam': '2'},
                 microphone_profiles={'rear': MicrophoneProfile(azimuth=290, elevation=30)}),
        ])


def observation(source, seat, t, level=-30, **kw):
    angles = {'a': {'primary': 20, 'rear': 210}, 'b': {'primary': 100, 'rear': 290}}
    return {'mic_id': source, 'azimuth': angles[seat][source] + (int(t*10) % 3)*.1,
            'elevation': 30, 'sample_time': t, 'activity': True, 'level': level, **kw}


def feed_pair(engine, t, first='a', second='a', levels=(-30, -30), **kw):
    results = []
    for source, seat, level in zip(('primary', 'rear'), (first, second), levels):
        value = engine.feed(observation(source, seat, t, level, **kw), t)
        if value:
            results.append(value)
    return results


@pytest.mark.parametrize('kind', ['legacy', 'new'])
def test_two_mics_confirm_one_physical_seat_and_switch(kind):
    engine = make_engine(shared_settings(kind), kind)
    commands = []
    for n in range(30):
        commands += feed_pair(engine, n/10)
    assert [c['zone'] for c in commands] == ['a']
    for n in range(30, 70):
        commands += feed_pair(engine, n/10, 'b', 'b')
    assert [c['zone'] for c in commands] == ['a', 'b']


@pytest.mark.parametrize('kind', ['legacy', 'new'])
def test_shared_seat_holds_on_disagreement_even_with_extreme_gain_difference(kind):
    engine = make_engine(shared_settings(kind), kind)
    for n in range(30):
        feed_pair(engine, n/10)
    assert engine.current == 'a'
    changes = []
    for n in range(30, 60):
        changes += feed_pair(engine, n/10, 'a', 'b', (-50, -10))
    assert not changes
    assert engine.current == 'a'


@pytest.mark.parametrize('kind', ['legacy', 'new'])
def test_shared_seat_holds_for_missing_freeze_and_overlap(kind):
    for mode in ('missing', 'frozen', 'overlap'):
        engine = make_engine(shared_settings(kind), kind)
        for n in range(30):
            t = n/10
            engine.feed(observation('primary', 'a', t), t)
            if mode == 'frozen':
                engine.feed(observation('rear', 'a', t, beam_freeze=True), t)
            if mode == 'overlap':
                engine.feed(observation('rear', 'a', t, overlap=True), t)
        assert engine.current is None, mode


def test_noise_floor_only_rises_from_non_speech_and_expires():
    cfg = TrackingSettings(level_threshold=-55)
    gate = QualityGate(cfg)
    for i in range(20):
        gate.feed({'azimuth': 20, 'elevation': 30, 'level': -42, 'activity': False}, i*.1)
    assert gate.noise_floor == -42
    reliability, reason, _ = gate.feed({'azimuth': 20, 'elevation': 30, 'level': -40}, 2)
    assert reliability == 0 and reason == 'below noise floor'
    gate.feed({'azimuth': 20, 'elevation': 30, 'level': -25, 'activity': True}, 2.1)
    assert gate.noise_floor == -42
    gate.feed({'azimuth': 20, 'elevation': 30, 'level': -25, 'activity': True}, 40)
    assert gate.noise_floor == -75


async def test_live_shared_follow_requires_independent_audio_and_speech(runtime, monkeypatch):
    from confdeck.models import Camera

    runtime.settings.tracking = shared_settings('new')
    runtime.settings.cameras = [Camera(id='cam', name='Camera', ip='192.168.1.20',
                                       control_enabled=True, control_protocol='visca')]
    class Mic:
        def status(self):
            return {'age': 0}
    class Audio:
        captures = {'primary': object(), 'rear': object()}
        active = False
        missing = False
        def evidence(self, source, timestamp, now):
            if self.missing and source == 'rear':
                return None
            return {'reliability': .9 if self.active else 0.}
        def status(self):
            return {s: 'ready' for s in self.captures}
        def stop(self):
            pass

    runtime.microphones['primary'] = Mic()
    try:
        runtime.follow.configure(runtime.settings.tracking)
        with pytest.raises(ValueError, match='independent audio'):
            runtime.follow.mode(True)
        runtime.multi_audio_enabled = True
        runtime.multi_audio = Audio()
        runtime.follow.configure(runtime.settings.tracking)
        with monkeypatch.context() as patch:
            clock = [1000.]
            patch.setattr('confdeck.follow.time.monotonic', lambda: clock[0])
            decisions = []
            patch.setattr(runtime.follow, 'dispatch', lambda decision: decisions.append(decision) if decision else None)
            runtime.follow.mode(True)
            for n in range(20):
                clock[0] = 1000+n*.1
                for source in ('primary', 'rear'):
                    runtime.follow.feed(observation(source, 'a', clock[0]))
            assert not decisions
            runtime.multi_audio.active = True
            for n in range(20, 50):
                clock[0] = 1000+n*.1
                for source in ('primary', 'rear'):
                    runtime.follow.feed(observation(source, 'a', clock[0]))
            assert [d['zone'] for d in decisions] == ['a']
            runtime.multi_audio.missing = True
            for n in range(50, 90):
                clock[0] = 1000+n*.1
                for source in ('primary', 'rear'):
                    runtime.follow.feed(observation(source, 'b', clock[0]))
            assert [d['zone'] for d in decisions] == ['a']
    finally:
        runtime.follow.live = False
        runtime.multi_audio = None
        runtime.microphones.clear()


@pytest.mark.parametrize('condition', ['quiet', 'hvac', 'clap', 'far_end', 'music'])
def test_non_speech_with_no_vad_evidence_cannot_lock(condition):
    engine = make_engine(shared_settings('new'), 'new', speech=True)
    for n in range(40):
        t = n/10
        level = {'quiet': -85, 'hvac': -38, 'clap': -5, 'far_end': -20, 'music': -25}[condition]
        for source in ('primary', 'rear'):
            ev = observation(source, 'a', t, level, speech={'reliability': 0})
            assert engine.feed(ev, t) is None
    assert engine.current is None


@pytest.mark.parametrize('level', [-75, -35])
def test_soft_and_normal_speech_can_lock_when_vad_is_present(level):
    engine = make_engine(shared_settings('new'), 'new', speech=True)
    decisions = []
    for n in range(25):
        t = n/10
        for source in ('primary', 'rear'):
            ev = observation(source, 'a', t, level, speech={'reliability': .9})
            result = engine.feed(ev, t)
            if result:
                decisions.append((t, result['zone']))
    assert [d[1] for d in decisions] == ['a']
    assert decisions[0][0] <= 1.2  # Simulated decision only, not physical camera latency.


def test_one_reflected_direction_cannot_override_other_mic():
    engine = make_engine(shared_settings('new'), 'new', speech=True)
    decisions = []
    for n in range(30):
        t = n/10
        for source, seat in [('primary', 'a'), ('rear', 'b')]:
            result = engine.feed(observation(source, seat, t, speech={'reliability': .9}), t)
            if result:
                decisions.append(result)
    assert not decisions


def test_correlated_wrong_directions_are_not_observable_as_reflection():
    # If both sensors report exactly the same readings as an actual B speaker,
    # direction and speech scalars contain no information to distinguish them.
    engine = make_engine(shared_settings('new'), 'new', speech=True)
    for n in range(30):
        t = n/10
        for source in ('primary', 'rear'):
            engine.feed(observation(source, 'b', t, speech={'reliability': .9}), t)
    assert engine.current == 'b'


async def test_stale_shared_decision_cannot_recall_camera(runtime):
    import time

    runtime.settings.tracking = shared_settings('new')
    runtime.follow.configure(runtime.settings.tracking)
    class Audio:
        reliability = .9
        def evidence(self, source, timestamp, now):
            return {'reliability': self.reliability}
        def stop(self):
            pass
    runtime.multi_audio = Audio()
    runtime.follow.live = True
    engine = runtime.follow.engine
    engine.current = 'a'
    now = time.monotonic()
    engine.votes = {'primary': (now-1, -30, 'a', True), 'rear': (now-1, -30, 'a', True)}
    decision = {'zone': 'a', 'presets': {'cam': '1'}, 'time': now, 'evidence_start': now-.5}
    try:
        runtime.follow.dispatch(decision)
        assert runtime.follow.task is None
        assert engine.current is None
        assert 'expired' in runtime.follow.log[-1]['message']
        engine.current = 'a'
        engine.votes = {'primary': (now, -30, 'a', True), 'rear': (now, -30, 'a', True)}
        runtime.multi_audio.reliability = 0
        runtime.follow.dispatch(decision)
        assert runtime.follow.task is None
        assert engine.current is None
    finally:
        runtime.follow.live = False
        runtime.multi_audio = None


def test_shared_replay_requires_recorded_independent_speech_evidence():
    from confdeck.evaluation.replay import replay
    cfg = shared_settings('new')
    rows = []
    for n in range(30):
        t = n/10
        for source in ('primary', 'rear'):
            rows.append({'kind': 'mic', 't': t, 'event': observation(source, 'a', t)})
    session = {'tag': 'test', 'rows': rows, 'labels': [{'start': 0, 'end': 3, 'seat': 'a', 'condition': 'normal'}]}
    assert not replay(session, cfg, 'new')['commands']
    for row in rows:
        row['event']['speech'] = {'reliability': .9}
    assert [command['zone'] for command in replay(session, cfg, 'new')['commands']] == ['a']


def test_shared_tuning_preview_does_not_invent_speech():
    from confdeck.tuning_preview import preview
    cfg = shared_settings('new')
    rows = []
    for n in range(30):
        t = n/10
        for source in ('primary', 'rear'):
            rows.append({'t': t, 'event': observation(source, 'a', t)})
    assert all(point['seat'] is None for point in preview(rows, cfg)['events'])


def test_experimental_fusion_rejects_new_shared_seat_model():
    from confdeck.seat_fusion.engine import SeatFusion
    with pytest.raises(ValueError, match='does not support shared'):
        SeatFusion(shared_settings())
