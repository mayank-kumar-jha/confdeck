"""Tests for the SSCv2 adapter: SSE parsing, reconnect, TOFU, field mapping, errors, redaction, and staged tests."""

import base64
import hashlib
import json
import ssl

import httpx
import pytest

from confdeck.microphone import (
    MockSSCv2Server,
    PositionParser,
    SSCv2Adapter,
)
from confdeck.models import MicSettings
from confdeck.security import SECRET_VALUES, redact

RESOURCE = "/api/audio/inputs/microphone/beam/direction"
SESSION = "/api/ssc/state/subscriptions/uuid-1234"


def _fake_cert_der() -> bytes:
    return b"\x05" * 32


def _fake_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def make_settings(**kwargs):
    defaults = dict(ip="192.168.1.30", port=443)
    defaults.update(kwargs)
    return MicSettings(**defaults)


def make_sse(*events) -> bytes:
    """Build an SSE payload from (event_type, data) tuples."""
    parts = []
    for ev_type, data in events:
        if ev_type:
            parts.append(f"event: {ev_type}\n")
        parts.append(f"data: {data}\n\n")
    return "".join(parts).encode()


# ---------------------------------------------------------------------------
# Core SSE parsing and position extraction
# ---------------------------------------------------------------------------


def test_position_parser_basic():
    s = MicSettings(ip="192.168.1.30", port=443)
    p = PositionParser(s)
    p.feed(json.dumps({RESOURCE: {"azimuth": 180, "elevation": 45}}).encode())
    assert p.position is not None
    assert p.position["azimuth"] == 180
    assert p.position["elevation"] == 45


def test_far_end_freeze_survives_partial_telemetry_until_explicitly_cleared():
    parser = PositionParser(make_settings())
    samples = []
    parser.on_sample = samples.append
    parser.consume({RESOURCE: {"azimuth": 90, "elevation": 45,
                               "beamFreezeActive": True}}, 100.0)
    assert samples[-1]["beam_freeze"] is True

    # Level-only and partial direction updates do not mean the far end stopped.
    parser.consume({"/api/audio/inputs/microphone/level": {"peak": -30}}, 100.1)
    assert parser.position["beam_freeze"] is True
    parser.consume({RESOURCE: {"azimuth": 91}}, 100.2)
    assert samples[-1]["beam_freeze"] is True

    # Freeze-only messages are meaningful even if the beam hasn't moved.
    revision = parser.direction_revision
    parser.consume({RESOURCE: {"beamFreezeActive": False}}, 100.3)
    assert parser.direction_revision > revision
    assert samples[-1]["beam_freeze"] is False
    parser.consume({RESOURCE: {"beamFreezeActive": True}}, 100.4)
    assert samples[-1]["beam_freeze"] is True


def test_position_parser_field_map():
    s = MicSettings(
        ip="192.168.1.30",
        port=443,
        field_map={"azimuth": "az", "elevation": "el"},
    )
    p = PositionParser(s)
    p.feed(json.dumps({RESOURCE: {"az": 90, "el": 30}}).encode())
    assert p.position is not None
    assert p.position["azimuth"] == 90
    assert p.position["elevation"] == 30


def test_position_parser_invert_and_offset():
    s = MicSettings(
        ip="192.168.1.30",
        port=443,
        invert=True,
        azimuth_offset=10,
    )
    p = PositionParser(s)
    p.feed(json.dumps({"/api/audio/inputs/microphone/beam/direction": {"azimuth": 100, "elevation": 45}}).encode())
    # invert: -100, offset: +10 -> -90 -> 270
    assert p.position["azimuth"] == pytest.approx(270)


def test_sse_split_chunks():
    """SSE data split across multiple feed() calls should still parse."""
    s = MicSettings(ip="192.168.1.30", port=443)
    p = PositionParser(s)
    payload = json.dumps({RESOURCE: {"azimuth": 200, "elevation": 60}})
    half = len(payload) // 2
    p.feed(payload[:half].encode())
    assert p.position is None  # incomplete
    p.feed(payload[half:].encode())
    assert p.position is not None
    assert p.position["azimuth"] == 200


def test_sse_multiple_objects_in_one_chunk():
    """Multiple JSON objects concatenated in one feed() call."""
    s = MicSettings(ip="192.168.1.30", port=443)
    p = PositionParser(s)
    obj1 = json.dumps({"/api/audio/inputs/microphone/beam/direction": {"azimuth": 100, "elevation": 30}})
    obj2 = json.dumps({RESOURCE: {"azimuth": 200, "elevation": 50}})
    p.feed((obj1 + "\n" + obj2 + "\n").encode())
    assert p.bad == 0
    assert p.position["azimuth"] == 200
    assert p.position["elevation"] == 50


def test_malformed_json_tolerated():
    s = MicSettings(ip="192.168.1.30", port=443)
    p = PositionParser(s)
    p.feed(b"{broken json}\n")
    assert p.bad >= 1
    # Good data after bad
    p.feed(json.dumps({RESOURCE: {"azimuth": 90, "elevation": 45}}).encode())
    assert p.position is not None


def test_secret_redaction_in_raw_viewer():
    """The raw viewer must not expose the device password."""
    password = "super-secret-pw-xyz"
    SECRET_VALUES.add(password)
    text = f'{{"Authorization": "Basic {base64.b64encode(f"api:{password}".encode()).decode()}"}}'
    result = redact(text)
    assert password not in result


def test_field_mapping_auto_detected():
    """Parser returns raw event data; unknown fields are stored but don't crash."""
    s = MicSettings(ip="192.168.1.30", port=443)
    p = PositionParser(s)
    p.feed(json.dumps({RESOURCE: {"az_deg": 90, "el_deg": 45, "active": True}}).encode())
    assert p.position is None
    assert len(p.raw) == 1


# ---------------------------------------------------------------------------
# SSCv2Adapter session() with httpx.MockTransport
# ---------------------------------------------------------------------------


async def test_sscv2_session_full_flow(monkeypatch):
    """Full happy path: identity, SSE open, PUT /add, message data."""
    calls = []
    sub_path = "/api/ssc/state/subscriptions/uuid-1234"
    add_path = sub_path + "/add"

    fake_der = _fake_cert_der()
    fake_fp = hashlib.sha256(fake_der).hexdigest()

    import confdeck.microphone as mic_mod

    async def fake_fetch(ip, port):
        return fake_der

    monkeypatch.setattr(mic_mod, "_fetch_cert_der", fake_fetch)
    monkeypatch.setattr(mic_mod, "_build_ssl_context_for_pinned", lambda der: _fake_ssl_ctx())

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path == "/api/device/identity":
            return httpx.Response(200, json={"product": "TeamConnect Ceiling Medium"})
        if request.url.path == "/api/ssc/version":
            return httpx.Response(200, json={"version": "1.0"})
        if request.url.path == "/api/ssc/state/subscriptions":
            payload = make_sse(
                ("open", json.dumps({"sessionUUID": "uuid-1234"})),
                ("message", json.dumps({RESOURCE: {"azimuth": 187, "elevation": 32}})),
            )
            return httpx.Response(
                200,
                headers={"Content-Location": sub_path, "Content-Type": "text/event-stream"},
                content=payload,
            )
        if request.method == "PUT" and request.url.path == add_path:
            return httpx.Response(200, json={})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )

    settings = make_settings(tls_fingerprint=fake_fp)
    adapter = SSCv2Adapter(settings, "secret")
    await adapter.session()

    assert adapter.parser.position is not None
    assert adapter.parser.position["azimuth"] == 187
    assert adapter.parser.position["elevation"] == 32
    assert ("PUT", add_path) in calls


async def test_sscv2_fingerprint_mismatch_raises(monkeypatch):
    """If the certificate fingerprint changes, session() should raise ValueError."""
    fake_der = _fake_cert_der()
    wrong_fp = "00" * 32

    import confdeck.microphone as mic_mod

    async def fake_fetch(ip, port):
        return fake_der

    monkeypatch.setattr(mic_mod, "_fetch_cert_der", fake_fetch)

    settings = make_settings(tls_fingerprint=wrong_fp)
    adapter = SSCv2Adapter(settings, "pw")
    with pytest.raises(ValueError, match="fingerprint"):
        await adapter.session()
    assert adapter.issue is not None
    assert adapter.issue["code"] == "mic_fingerprint_changed"


async def test_sscv2_tofu_saves_fingerprint(monkeypatch):
    """When tls_fingerprint is empty, TOFU should call fingerprint_callback."""
    fake_der = _fake_cert_der()
    saved = []

    import confdeck.microphone as mic_mod

    async def fake_fetch(ip, port):
        return fake_der

    monkeypatch.setattr(mic_mod, "_fetch_cert_der", fake_fetch)
    monkeypatch.setattr(mic_mod, "_build_ssl_context_for_pinned", lambda der: _fake_ssl_ctx())

    def handler(request):
        if request.url.path == "/api/device/identity":
            return httpx.Response(200, json={"product": "TeamConnect Ceiling Medium"})
        if request.url.path == "/api/ssc/version":
            return httpx.Response(200, json={"version": "1.0"})
        if request.url.path == "/api/ssc/state/subscriptions":
            data = make_sse(
                ("open", json.dumps({"sessionUUID": "uuid-1"})),
                ("message", json.dumps({RESOURCE: {"azimuth": 90, "elevation": 45}})),
            )
            return httpx.Response(
                200,
                headers={"Content-Location": "/api/ssc/state/subscriptions/uuid-1", "Content-Type": "text/event-stream"},
                content=data,
            )
        if request.method in ("PUT", "DELETE"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )

    settings = make_settings(tls_fingerprint="")
    adapter = SSCv2Adapter(settings, "pw", fingerprint_callback=lambda fp, der: saved.append(fp))
    await adapter.session()
    assert len(saved) == 1
    assert saved[0] == hashlib.sha256(fake_der).hexdigest()


async def test_sscv2_reconnect_creates_new_subscription(monkeypatch):
    """On session restart, a new subscription UUID is used."""
    fake_der = _fake_cert_der()

    import confdeck.microphone as mic_mod

    async def fake_fetch(ip, port):
        return fake_der

    monkeypatch.setattr(mic_mod, "_fetch_cert_der", fake_fetch)
    monkeypatch.setattr(mic_mod, "_build_ssl_context_for_pinned", lambda der: _fake_ssl_ctx())

    call_count = {"n": 0}
    uuids = []

    def handler(request):
        if request.url.path == "/api/device/identity":
            return httpx.Response(200, json={"product": "TeamConnect Ceiling Medium"})
        if request.url.path == "/api/ssc/version":
            return httpx.Response(200, json={"version": "1.0"})
        if request.url.path == "/api/ssc/state/subscriptions":
            call_count["n"] += 1
            uuid = f"uuid-{call_count['n']}"
            uuids.append(uuid)
            data = make_sse(
                ("open", json.dumps({"sessionUUID": uuid})),
                ("message", json.dumps({RESOURCE: {"azimuth": 10.0 * call_count["n"], "elevation": 45}})),
            )
            return httpx.Response(
                200,
                headers={
                    "Content-Location": f"/api/ssc/state/subscriptions/{uuid}",
                    "Content-Type": "text/event-stream",
                },
                content=data,
            )
        if request.method in ("PUT", "DELETE"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )

    settings = make_settings(tls_fingerprint=hashlib.sha256(fake_der).hexdigest())
    adapter = SSCv2Adapter(settings, "pw")
    await adapter.session()
    await adapter.session()
    assert len(uuids) == 2
    assert uuids[0] != uuids[1]


async def test_sscv2_unexpected_model_warns(monkeypatch):
    """A device that's not a Ceiling Medium should result in a warning in identity."""
    fake_der = _fake_cert_der()

    import confdeck.microphone as mic_mod

    async def fake_fetch(ip, port):
        return fake_der

    monkeypatch.setattr(mic_mod, "_fetch_cert_der", fake_fetch)
    monkeypatch.setattr(mic_mod, "_build_ssl_context_for_pinned", lambda der: _fake_ssl_ctx())

    def handler(request):
        if request.url.path == "/api/device/identity":
            return httpx.Response(200, json={"product": "TeamConnect Bar 3"})
        if request.url.path == "/api/ssc/version":
            return httpx.Response(200, json={"version": "1.0"})
        if request.url.path == "/api/ssc/state/subscriptions":
            data = make_sse(
                ("open", json.dumps({"sessionUUID": "uuid-x"})),
                ("message", json.dumps({RESOURCE: {"azimuth": 90, "elevation": 45}})),
            )
            return httpx.Response(
                200,
                headers={
                    "Content-Location": "/api/ssc/state/subscriptions/uuid-x",
                    "Content-Type": "text/event-stream",
                },
                content=data,
            )
        if request.method in ("PUT", "DELETE"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )

    settings = make_settings(tls_fingerprint=hashlib.sha256(fake_der).hexdigest())
    adapter = SSCv2Adapter(settings, "pw")
    await adapter.session()
    assert "unexpected model" in adapter.identity.lower() or "TeamConnect Bar 3" in adapter.identity


# ---------------------------------------------------------------------------
# Integration tests with MockSSCv2Server (real HTTPS/TLS over loopback)
# ---------------------------------------------------------------------------


async def test_mock_sscv2_server_staged_test_all_stages():
    """Run staged_test() against local MockSSCv2Server and check all 6 stages pass."""
    server = MockSSCv2Server(password="secret123")
    port = await server.start()
    try:
        settings = MicSettings(
            ip="127.0.0.1",
            port=port,
            tls_fingerprint=server.fingerprint,
        )
        adapter = SSCv2Adapter(settings, "secret123")
        stages = await adapter.staged_test()
        assert len(stages) == 6
        for s in stages:
            assert s["ok"] is True, f"Stage {s['stage']} ({s['name']}) failed: {s['detail']}"
    finally:
        await server.stop()


async def test_mock_sscv2_server_auth_failure_stage3():
    """Wrong password stops at stage 3 with 401 and fix hint."""
    server = MockSSCv2Server(password="correct-password")
    port = await server.start()
    try:
        settings = MicSettings(
            ip="127.0.0.1",
            port=port,
            tls_fingerprint=server.fingerprint,
        )
        adapter = SSCv2Adapter(settings, "wrong-password")
        stages = await adapter.staged_test()
        assert len(stages) == 3
        assert stages[0]["ok"] is True
        assert stages[1]["ok"] is True
        assert stages[2]["ok"] is False
        assert stages[2]["stage"] == 3
        assert "401" in stages[2]["detail"]
        assert "Access" in stages[2]["fix"]
    finally:
        await server.stop()


async def test_mock_sscv2_server_not_found_stage5():
    """Subscription path returning 404 stops at stage 5 with fix hint."""
    server = MockSSCv2Server(password="correct-password")
    server.error_mode = "not_found"
    port = await server.start()
    try:
        settings = MicSettings(
            ip="127.0.0.1",
            port=port,
            tls_fingerprint=server.fingerprint,
        )
        adapter = SSCv2Adapter(settings, "correct-password")
        stages = await adapter.staged_test()
        assert len(stages) == 5
        assert stages[4]["ok"] is False
        assert stages[4]["stage"] == 5
        assert "404" in stages[4]["detail"] or "not found" in stages[4]["detail"].lower()
    finally:
        await server.stop()


async def test_api_mic_test_endpoint(runtime, client):
    """Test the /api/mic/test HTTP endpoint via FastAPI test client."""
    # Without medium connected -> 400
    res = await client.post("/api/mic/test")
    assert res.status_code == 400
    assert "Connect a Ceiling Medium first" in res.json()["error"]["detail"]

    # Connect mock server as medium adapter -> staged test runs
    server = MockSSCv2Server(password="apipass")
    port = await server.start()
    try:
        settings = MicSettings(
            ip="127.0.0.1",
            port=port,
            tls_fingerprint=server.fingerprint,
        )
        runtime.mic = SSCv2Adapter(settings, "apipass")
        res = await client.post("/api/mic/test")
        assert res.status_code == 200
        data = res.json()
        assert "stages" in data
        assert len(data["stages"]) == 6
        assert all(s["ok"] for s in data["stages"])
    finally:
        await server.stop()

