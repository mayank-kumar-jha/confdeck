import builtins
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from confdeck.speech_audio import AudioCapture


def test_off_opens_nothing():
    result = subprocess.run([sys.executable, '-c',
        "import sys; from confdeck.speech_audio import AudioCapture; "
        "c=AudioCapture('missing.onnx'); assert c.snapshot()['status']=='Off'; "
        "assert not any(m in sys.modules for m in ('sounddevice','onnxruntime','confdeck.speech'))"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_missing_model_falls_back(tmp_path):
    capture = AudioCapture(tmp_path/'missing.onnx')
    capture.start()
    capture.thread.join(2)
    assert not capture.thread.is_alive()
    assert 'level-only gate' in capture.snapshot()['status']
    assert capture.snapshot()['frame'] is None
    capture.stop()


def test_mock_input_channel_memory_only_and_stop(monkeypatch):
    capture = AudioCapture('mock.onnx', device=3, channel=2)
    processed = threading.Event()
    seen = []
    class Model:
        def __init__(self, path):
            pass
        def reset(self):
            pass
        def process(self, samples):
            seen.append(float(samples[0]))
            processed.set()
            return dict(level=-60., probability=.8, clipping=False, no_signal=False)
    class Stream:
        def __init__(self, **kwargs):
            assert kwargs['samplerate'] == 16000 and kwargs['channels'] == 2
            assert kwargs['blocksize'] == 512 and kwargs['device'] == 3
            self.callback = kwargs['callback']
        def __enter__(self):
            samples = np.zeros((512, 2), dtype=np.float32)
            samples[:, 1] = .25
            self.callback(samples, 512, SimpleNamespace(inputBufferAdcTime=1., currentTime=1.), False)
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr('confdeck.speech.SileroFrames', Model)
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(
        InputStream=Stream, check_input_settings=lambda **kw: None))
    def forbidden(*a, **kw):
        raise AssertionError('Audio path must not open files')
    monkeypatch.setattr(builtins, 'open', forbidden)
    capture.start()
    assert processed.wait(2)
    capture.stop()
    assert seen == [.25]
    assert not capture.thread.is_alive()
    assert not capture.frames
    assert capture.snapshot()['status'] == 'Off'


def test_stale_frames_and_channel_validation():
    capture = AudioCapture('mock')
    capture.latest = dict(time=time.monotonic()-1, level=-60)
    assert capture.snapshot()['frame'] is None
    assert 'level-only' in capture.snapshot()['status']
    with pytest.raises(ValueError):
        AudioCapture('mock', channel=0)


def test_device_error_reconnects_and_clears_evidence(monkeypatch):
    capture = AudioCapture('mock')
    attempts = []
    class Model:
        def __init__(self, path):
            pass
        def reset(self):
            pass
    def check(**kwargs):
        attempts.append(kwargs)
        if len(attempts) >= 2:
            capture.stop_event.set()
        raise OSError('device disconnected')
    original_wait = capture.stop_event.wait
    monkeypatch.setattr(capture.stop_event, 'wait', lambda timeout: original_wait(.001))
    monkeypatch.setattr('confdeck.speech.SileroFrames', Model)
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(check_input_settings=check))
    capture.start()
    capture.thread.join(2)
    assert len(attempts) == 2
    assert capture.snapshot()['frame'] is None
    assert 'level-only' in capture.snapshot()['status']
    capture.stop()
