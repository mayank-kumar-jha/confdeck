import asyncio
import json
import os
import time

import pytest
from pydantic import ValidationError

from confdeck.errors import CATALOG, classify_camera, problem
from confdeck.microphone import MockAdapter, PositionParser
from confdeck.models import Camera, MicSettings, Settings, local_ip, private_subnet
from confdeck.offline import network_audit
from confdeck.security import SecretStore, redact
from confdeck.store import Store


@pytest.mark.parametrize(
    "ip", ["8.8.8.8", "169.254.169.254", "example.com", "0.0.0.0", "100.64.0.1", "224.1.1.1"]
)
def test_public_and_metadata_addresses_rejected(ip):
    with pytest.raises(ValueError):
        local_ip(ip)


@pytest.mark.parametrize(
    "network", ["192.168.0.0/16", "192.168.1.0/25", "8.8.8.0/24", "127.0.0.0/24"]
)
def test_scan_is_exactly_private_24(network):
    with pytest.raises(ValueError):
        private_subnet(network)


def test_duplicate_camera_rejected():
    with pytest.raises(ValidationError):
        Settings(cameras=[Camera(ip="192.168.1.20"), Camera(ip="192.168.1.20")])


@pytest.mark.parametrize(
    "path",
    [
        "rtsp://user:pass@192.168.1.2/a",
        "/stream?password=secret",
        "/stream\nmalformed",
        "//user@host",
    ],
)
def test_credentials_not_allowed_in_stream_path(path):
    with pytest.raises(ValidationError):
        Camera(ip="192.168.1.20", path=path)


def test_parser_fragments_duplicates_missing_and_invalid_fields():
    p = PositionParser(MicSettings())
    p.feed(b'{"/api/audio/inputs/microphone/beam/direction":{"azimuth":')
    p.feed(b"231}}\n")
    assert p.position is None
    p.feed(b'{"/api/audio/inputs/microphone/beam/direction":{"elevation":42}}')
    assert p.position["azimuth"] == 231
    assert p.position["elevation"] == 42
    events = p.queue.qsize()
    p.feed(b'{"/api/audio/inputs/microphone/beam/direction":{"elevation":42,"azimuth":231}}')
    assert p.queue.qsize() == events
    for bad in (
        b'{"/api/audio/inputs/microphone/beam/direction":null}',
        b'{"/api/audio/inputs/microphone/beam/direction":{"azimuth":true,"elevation":null}}',
        b'{"/api/audio/inputs/microphone/beam/direction":{"azimuth":NaN,"elevation":999}}',
        b"[]",
        b"garbage",
    ):
        p.feed(bad)
    assert p.position["azimuth"] == 231
    assert p.position["elevation"] == 42


def test_parser_recovers_after_partial_garbage_and_bounds_memory():
    p = PositionParser(MicSettings())
    p.feed(b'{"broken":')
    for i in range(1000):
        p.feed(json.dumps({"/api/audio/inputs/microphone/beam/direction": {"azimuth": i % 360, "elevation": 25}}).encode())
    assert p.position["azimuth"] == 999 % 360
    assert len(p.raw) == 200
    assert len(p.history) <= 120
    assert p.queue.qsize() <= 16
    assert len(p.buffer) <= 16384


def test_timestamped_out_of_order_messages_and_orientation():
    p = PositionParser(MicSettings(invert=True, azimuth_offset=90, estimate_position=True))
    p.consume({"timestamp": 2, "/api/audio/inputs/microphone/beam/direction": {"azimuth": 30, "elevation": 45}}, time.monotonic())
    p.consume({"timestamp": 1, "/api/audio/inputs/microphone/beam/direction": {"azimuth": 200}}, time.monotonic())
    assert p.position["azimuth"] == 60
    assert p.position["estimated_xy"] == [1.56, 0.9]


def test_sscv2_resource_notifications():
    p = PositionParser(MicSettings())
    p.feed(b'{"/api/audio/inputs/microphone/beam/direction":{"azimuth":134,"elevation":48}}')
    assert p.position["azimuth"] == 134


async def test_mock_mic_loss_garbage_and_recovery():
    adapter = MockAdapter(MicSettings())
    try:
        await adapter.connect()
        for _ in range(20):
            if adapter.parser.position:
                break
            await asyncio.sleep(0.05)
        assert adapter.status()["state"] == "Connected"
        adapter.garbage = True
        await asyncio.sleep(0.2)
        assert adapter.parser.bad > 0
        assert adapter.status()["state"] == "Connected"
        adapter.drop = True
        await asyncio.sleep(0.15)
        old = adapter.parser.last
        await asyncio.sleep(0.15)
        assert adapter.parser.last == old
        adapter.parser.last -= 10
        assert adapter.status()["state"] == "Reconnecting"
        adapter.drop = False
        await asyncio.sleep(0.2)
        assert adapter.status()["state"] == "Connected"
    finally:
        await adapter.close()


def test_secret_file_and_redaction(tmp_path):
    store = Store(tmp_path)
    secrets = SecretStore(tmp_path)
    secrets.set("camera", username="operator", password="Secret!982")
    assert SecretStore(tmp_path).get("camera")["password"] == "Secret!982"
    if os.name == "nt":
        assert b"Secret!982" not in (tmp_path / "secrets.bin").read_bytes()
    else:
        assert (tmp_path / "secrets.bin").stat().st_mode & 0o077 == 0
    output = str(
        redact(
            {
                "password": "Secret!982",
                "message": "rtsp://operator:Secret!982@192.168.1.20/live",
                "Authorization": "Basic xyz",
            }
        )
    )
    assert "Secret!982" not in output and "Basic xyz" not in output
    assert "Secret!982" not in store.load().model_dump_json()
    store.close()


def test_corrupt_settings_restore_valid_backup(tmp_path):
    store = Store(tmp_path)
    store.save(Settings(room_name="Boardroom"))
    store.db.execute("UPDATE settings SET body='not json'")
    store.db.commit()
    assert store.load().room_name == "Boardroom"
    assert store.warning
    store.close()


def test_corrupt_database_recovery(tmp_path):
    store = Store(tmp_path)
    store.save(Settings(room_name="Saved room"))
    store.close()
    (tmp_path / "settings.db").write_bytes(b"corrupt database")
    restored = Store(tmp_path)
    assert restored.load().room_name == "Saved room"
    assert restored.warning
    restored.close()


@pytest.mark.parametrize(
    "message,code",
    [
        ("401 Unauthorized", "password"),
        ("404 not found", "path"),
        ("453 Not Enough Bandwidth", "limit"),
        ("Connection refused", "limit"),
        ("read timed out", "limit"),
    ],
)
def test_camera_error_explanations(message, code):
    assert classify_camera(message) == code
    p = problem(code)
    assert all(p[k] for k in ("title", "cause", "fix"))


def test_every_error_has_plain_language_recovery():
    for code in CATALOG:
        assert problem(code)["fix"]
        assert "Traceback" not in str(problem(code))


def test_runtime_blocks_public_connect_and_dns():
    with pytest.raises(PermissionError):
        network_audit("socket.connect", (None, ("8.8.8.8", 443)))
    with pytest.raises(PermissionError):
        network_audit("socket.getaddrinfo", ("example.com", 443))
    network_audit("socket.connect", (None, ("192.168.1.20", 554)))


def test_no_runtime_cloud_assets():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "confdeck" / "static"
    for file in root.glob("*.html"):
        assert 'src="http' not in file.read_text()
        assert 'href="https:' not in file.read_text()
