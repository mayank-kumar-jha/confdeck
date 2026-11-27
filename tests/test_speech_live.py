import time
from types import SimpleNamespace

import pytest

from confdeck.evaluation.synthetic import generate
from confdeck.follow import Follow
from confdeck.runtime import Runtime
from confdeck.speech_audio import AudioCapture


def test_reset_audio_marker_prevents_reusing_previous_microphone_stream(tmp_path, monkeypatch):
    monkeypatch.setenv('CONFDECK_SPEECH_ENABLED', '1')
    monkeypatch.setenv('CONFDECK_SPEECH_MODEL', 'model.onnx')
    monkeypatch.setenv('CONFDECK_SPEECH_SDP', 'old-microphone.sdp')
    (tmp_path / 'speech-source-reset.json').write_text('{}')
    runtime = Runtime(tmp_path)
    assert runtime.speech_capture is None
    assert runtime.speech_status()['enabled']
    assert not runtime.speech_status()['healthy']
    assert 'configuration after reset' in runtime.speech_status()['status']


def test_capture_rejects_old_or_future_frames():
    capture = AudioCapture("unused")
    capture.latest = {"time": time.monotonic() - 10, "no_signal": False}
    capture.latest_received = time.monotonic()
    assert capture.snapshot()["frame"] is None
    capture.latest["time"] = time.monotonic() + 10
    assert capture.snapshot()["frame"] is None


def test_fresh_matching_speech_and_missing_audio_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFDECK_SPEECH_ENABLED", "1")
    runtime = Runtime(tmp_path)
    _, settings = generate()
    settings.engine = "new"
    runtime.settings.tracking = settings
    runtime.follow.configure(settings)
    decisions = []
    runtime.follow.dispatch = decisions.append
    clock = [100.]
    monkeypatch.setattr("confdeck.follow.time.monotonic", lambda: clock[0])
    # Mimic a fresh low-level voice. Speech path bypasses the much higher
    # telemetry threshold but keeps the spherical confirmation thresholds.
    runtime.speech_evidence = lambda timestamp: {"reliability": .8}
    for i in range(22):
        clock[0] = 100 + i*.1
        runtime.follow.feed({"sample_time": clock[0], "azimuth": 20+(i%3)*.1,
                             "elevation": 20, "level": -68})
    assert runtime.follow.engine.current == "a"
    assert any(d and d["zone"] == "a" for d in decisions)
    runtime.speech_evidence = lambda timestamp: None
    decisions.clear()
    for i in range(10):
        clock[0] += .1
        runtime.follow.feed({"sample_time":clock[0], "azimuth":200+(i%3)*.1,
                             "elevation":20, "level":-20})
    assert runtime.follow.engine.current is None
    assert not any(decisions)


def test_non_speech_never_locks_even_if_loud_and_prolonged(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFDECK_SPEECH_ENABLED", "1")
    runtime = Runtime(tmp_path)
    _, settings = generate()
    settings.engine = "new"
    runtime.settings.tracking = settings
    runtime.follow.configure(settings)
    decisions = []
    runtime.follow.dispatch = decisions.append
    clock = [100.]
    monkeypatch.setattr("confdeck.follow.time.monotonic", lambda: clock[0])
    runtime.speech_evidence = lambda timestamp: {"reliability": 0}
    for i in range(100):
        clock[0] = 100+i*.1
        runtime.follow.feed({"sample_time":clock[0], "azimuth":20+(i%3)*.1,
                             "elevation":20, "level":-15})
    assert runtime.follow.engine.current is None
    assert not any(decisions)
