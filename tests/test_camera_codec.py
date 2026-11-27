"""Tests for camera codec fallback: all H.265, one H.264, timeouts.

ffprobe is mocked at the subprocess level so no real network or binary is required.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from confdeck.media import (
    Media,
    _score_h264,
    probe_alternative_streams,
    video_status,
)
from confdeck.models import Camera

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stream_json(codec="hevc", width=1920, height=1080, fps="25/1"):
    return json.dumps({"streams": [{"codec_name": codec, "width": width, "height": height, "avg_frame_rate": fps, "nb_read_frames": "1"}]}).encode()


def _make_process(stdout_data: bytes, returncode: int = 0):
    """Return a mock asyncio subprocess whose communicate() yields (stdout_data, b'')."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_data, b""))
    proc.wait = AsyncMock(return_value=returncode)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# video_status unit tests
# ---------------------------------------------------------------------------

def test_video_status_h264():
    result = video_status({"codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "25/1"})
    assert result["state"] == "Online"
    assert result["codec"] == "h264"
    assert result["issue"] is None


def test_video_status_hevc_returns_unsupported():
    result = video_status({"codec_name": "hevc", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"})
    assert result["state"] == "Unsupported codec"
    assert "H.265" in result["issue"]["cause"]


def test_video_status_unknown_codec_returns_waiting():
    result = video_status({"codec_name": "unknown"})
    assert result["state"] == "Waiting for camera"


async def test_decoder_warnings_do_not_corrupt_probe_json():
    from confdeck.media import _probe_stream_direct
    process = _make_process(_stream_json("h264"))
    process.communicate = AsyncMock(return_value=(
        _stream_json("h264"), b"[h264] non-existing PPS 0 referenced\n"))
    with patch("confdeck.media.spawn", new=AsyncMock(return_value=process)) as spawn:
        result = await _probe_stream_direct("ffprobe", "rtsp://127.0.0.1:8554/camera")
    assert spawn.call_args.kwargs["separate_stderr"] is True
    assert result["codec"] == "h264"
    assert result["frames"] == 1


def test_configured_camera_sources_are_kept_connected_without_previews():
    class SecretStore:
        @staticmethod
        def get(_identity):
            return {}

    media = Media.__new__(Media)
    media.secrets = SecretStore()
    source = media.source(Camera(ip="192.168.1.50"))
    assert source["sourceOnDemand"] is False
    assert "sourceOnDemandCloseAfter" not in source


# ---------------------------------------------------------------------------
# _score_h264 unit tests
# ---------------------------------------------------------------------------

def test_score_prefers_1080p():
    s1080 = {"width": 1920, "height": 1080, "fps": 25}
    s720 = {"width": 1280, "height": 720, "fps": 25}
    assert _score_h264(s1080) > _score_h264(s720)


def test_score_prefers_25_30fps():
    s_good_fps = {"width": 1280, "height": 720, "fps": 25}
    s_bad_fps = {"width": 1280, "height": 720, "fps": 15}
    assert _score_h264(s_good_fps) > _score_h264(s_bad_fps)


# ---------------------------------------------------------------------------
# Scenario 1: all streams are H.265 — should return codec_streams table
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_h265_returns_codec_table():
    """When every candidate path returns HEVC, probe() should return
    state='Unsupported codec' with a non-empty codec_streams list and no
    auto-selection."""
    cam = Camera(ip="192.168.1.50", path="/rtpstream/config1")
    hevc_output = _stream_json("hevc")

    call_count = 0

    async def fake_spawn(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call = relay probe → HEVC
        # Subsequent = direct alt-stream probes → also HEVC
        return _make_process(hevc_output, returncode=0)

    with (
        patch("confdeck.media.spawn", side_effect=fake_spawn),
        patch("confdeck.media.binary", return_value="/fake/ffprobe"),
        patch("confdeck.media.terminate", new=AsyncMock()),
    ):
        # Build a minimal Media instance without real httpx/mediamtx.
        media = _fake_media()
        media.configure = AsyncMock()
        result = await media.probe(cam)

    assert result["state"] == "Unsupported codec"
    assert "codec_streams" in result
    assert len(result["codec_streams"]) > 0
    # No H.264 found → no auto-select
    assert result.get("codec_autoselect") is None
    # The instruction text is embedded in the fix message
    assert "H.264" in result["issue"]["fix"]
    assert "retry" in result["issue"]["fix"].lower()


# ---------------------------------------------------------------------------
# Scenario 2: one path returns H.264 — should auto-select it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_h264_path_is_autoselected():
    """When exactly one candidate path returns H.264, probe() should
    auto-select it and return state='Online' with codec_autoselect info."""

    cam = Camera(ip="192.168.1.51", path="/rtpstream/config1")
    hevc_output = _stream_json("hevc")
    h264_output = _stream_json("h264", 1920, 1080, "25/1")

    # We'll make only /rtpstream/config2 return H.264.
    h264_path = "/rtpstream/config2"
    call_index = 0

    async def fake_spawn(*args, **kwargs):
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            # First call: relay probe → HEVC (triggers fallback)
            return _make_process(hevc_output, returncode=0)
        # Direct probes: H.264 only for config2
        if media.configure.call_args.args[0].path == h264_path:
            return _make_process(h264_output, returncode=0)
        return _make_process(hevc_output, returncode=0)

    with (
        patch("confdeck.media.spawn", side_effect=fake_spawn),
        patch("confdeck.media.binary", return_value="/fake/ffprobe"),
        patch("confdeck.media.terminate", new=AsyncMock()),
    ):
        media = _fake_media()
        media.configure = AsyncMock()
        result = await media.probe(cam)

    assert result["state"] == "Online"
    assert result["codec"] == "h264"
    assert result.get("codec_autoselect") is not None
    assert result["codec_autoselect"]["chosen_path"] == h264_path
    assert result["active_path"] == h264_path
    # The camera object's path should have been updated.
    assert cam.path == h264_path


# ---------------------------------------------------------------------------
# Scenario 3: all probes time out — graceful handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_probes_timeout_graceful():
    """When all alternative-stream probes time out, probe() should return
    state='Unsupported codec' with timed_out=True entries in codec_streams."""
    cam = Camera(ip="192.168.1.52", path="/rtpstream/config1")
    hevc_output = _stream_json("hevc")
    call_index = 0

    async def fake_spawn(*args, **kwargs):
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            # Relay probe → HEVC
            return _make_process(hevc_output, returncode=0)
        # Direct probes → timeout (communicate raises TimeoutError)
        proc = _make_process(b"", returncode=1)
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        return proc

    with (
        patch("confdeck.media.spawn", side_effect=fake_spawn),
        patch("confdeck.media.binary", return_value="/fake/ffprobe"),
        patch("confdeck.media.terminate", new=AsyncMock()),
    ):
        media = _fake_media()
        media.configure = AsyncMock()
        result = await media.probe(cam)

    assert result["state"] == "Unsupported codec"
    assert "codec_streams" in result
    # All timed out entries should be present
    timed_out = [s for s in result["codec_streams"] if s.get("timed_out")]
    assert len(timed_out) > 0
    # No H.264 autoselect
    assert result.get("codec_autoselect") is None


# ---------------------------------------------------------------------------
# probe_alternative_streams unit test (isolated)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_alternative_streams_selects_best_h264():
    """probe_alternative_streams should pick the highest-scoring H.264 stream."""

    cam = Camera(ip="192.168.1.53", path="/rtpstream/config1")

    # Map path → (codec, w, h, fps)
    fake_streams = {
        "/rtpstream/config1": ("hevc", 1920, 1080, "25/1"),
        "/rtpstream/config2": ("h264", 1280, 720, "25/1"),
        "/rtpstream/config3": ("h264", 1920, 1080, "25/1"),  # best
        "/Streaming/Channels/101": ("hevc", 1920, 1080, "25/1"),
    }

    async def fake_probe_direct(executable, url):
        for path, (codec, w, h, fps_str) in fake_streams.items():
            if path in url:
                num, den = fps_str.split("/")
                return {
                    "timed_out": False,
                    "codec": codec,
                    "width": w,
                    "height": h,
                    "fps": round(float(num) / float(den), 1),
                }
        return {"timed_out": False, "codec": None, "width": None, "height": None, "fps": None}

    with patch("confdeck.media._probe_stream_direct", side_effect=fake_probe_direct):
        result = await probe_alternative_streams("/fake/ffprobe", cam, None)

    assert result["selected"] == "/rtpstream/config3"
    assert result["selected_info"]["width"] == 1920
    h264_paths = [s["path"] for s in result["streams"] if s.get("codec") == "h264"]
    assert len(h264_paths) >= 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fake_media():
    """Construct a Media instance with mocked async clients so probes don't hit network."""
    import httpx

    settings = MagicMock()
    settings.cameras = []
    secrets = MagicMock()
    secrets.get = MagicMock(return_value={})
    media = Media.__new__(Media)
    media.root = None
    media.settings = settings
    media.secrets = secrets
    media.display_host = "127.0.0.1"
    media.process = None
    media.task = None
    media.state = "Connected"
    media.issue = None
    media.restarts = 0
    media.statuses = {}
    media.last_errors = {}
    media.config_lock = asyncio.Lock()
    media.probe_locks = {}
    media.probe_serial = asyncio.Semaphore(1)
    media.transports = {}
    media.on_failure = None
    media.stopping = False
    media.demo_tasks = []
    media.client = MagicMock(spec=httpx.AsyncClient)
    return media
