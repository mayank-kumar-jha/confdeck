import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_manager():
    path = Path(__file__).resolve().parents[1] / 'artifacts/mini31-candidate/omnitrack-sennheiser/backend_services/stream_manager.py'
    spec = importlib.util.spec_from_file_location('candidate_stream_manager', path)
    module = importlib.util.module_from_spec(spec)
    with patch('signal.signal'):
        spec.loader.exec_module(module)
    return module


def test_hevc_prefers_same_camera_h264_substream():
    manager = load_manager()
    with patch.object(manager.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0, stdout='hevc\n'),
                                                              SimpleNamespace(returncode=0, stdout='h264\n')]):
        source, transcode = manager.browser_stream('rtsp://camera.local/stream1')
    assert source == 'rtsp://camera.local/stream2'
    assert transcode is False


def test_hevc_without_compatible_substream_gets_h264_conversion():
    manager = load_manager()
    with patch.object(manager.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0, stdout='hevc\n'),
                                                              SimpleNamespace(returncode=1, stdout='')]):
        source, transcode = manager.browser_stream('rtsp://camera.local/stream1')
    assert source == 'rtsp://camera.local/stream1'
    assert transcode is True
