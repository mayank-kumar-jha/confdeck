import json
import time

import httpx

from confdeck.app import create_app
from confdeck.models import Camera


async def test_local_admin_without_login_preserves_csrf(runtime, client):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://127.0.0.1"
    ) as unauth:
        assert (await unauth.get("/api/state")).status_code == 200
        assert (await unauth.post("/api/login", json={"pin": runtime.auth.pin})).status_code == 403
    assert (
        await client.post(
            "/api/settings",
            json={"room_name": "Changed"},
            headers={"origin": "https://evil.example"},
        )
    ).status_code == 403
    assert (await client.get("/api/state", headers={"host": "evil.example"})).status_code == 403
    assert (await client.get("/")).headers["content-security-policy"].find(
        "connect-src 'self'"
    ) >= 0


async def test_display_listener_has_no_admin_routes(runtime, client):
    connect = (await client.post("/api/display/connect")).json()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime, display=True)),
        base_url="http://127.0.0.1",
        headers={"x-confdeck": "1"},
    ) as tv:
        assert (await tv.get("/view/state")).status_code == 401
        assert (await tv.post("/pair", json={"code": "000bad"})).status_code == 401
        assert (
            await tv.post("/pair", json={"code": connect["code"], "name": "TV"})
        ).status_code == 200
        assert (await tv.get("/api/state")).status_code == 404
        assert (await tv.get("/static/app.js")).status_code == 404
        assert (await tv.get("/docs")).status_code == 404
        assert (await tv.get("/view/state")).status_code == 200
        await tv.post("/view/heartbeat", json={"fps": 30, "latency_ms": 100, "transport": "WebRTC"})
        assert runtime.displays()[0]["state"] == "Live"
        identity = runtime.displays()[0]["id"]
        await client.delete("/api/displays/" + identity)
        assert (await tv.get("/view/state")).status_code == 401


async def test_credentials_never_appear_in_state_or_validation(runtime, client, monkeypatch):
    async def probe(*args, **kwargs):
        return {}

    monkeypatch.setattr(runtime.media, "probe", probe)
    result = await client.post(
        "/api/cameras",
        json={"ip": "192.168.1.20", "password": "VerySecret123", "username": "camerauser"},
    )
    assert result.status_code == 200
    assert "VerySecret123" not in (await client.get("/api/state")).text
    assert "VerySecret123" not in (await client.get("/api/diagnostics")).text
    bad = await client.post("/api/cameras", json={"ip": "8.8.8.8", "password": "VerySecret123"})
    assert bad.status_code == 400
    assert "VerySecret123" not in bad.text
    duplicate = await client.post("/api/cameras", json={"ip": "192.168.1.20"})
    assert duplicate.json()["already_added"]
    assert len(runtime.settings.cameras) == 1
    assert (
        await client.patch("/api/cameras/" + result.json()["id"], json={"name": "North camera"})
    ).status_code == 200
    assert runtime.settings.cameras[0].name == "North camera"


async def test_pairing_expiry_and_brute_force(runtime):
    code = runtime.auth.pairing()
    runtime.auth.code_until = time.time() - 1
    assert runtime.auth.pair(code, "TV") is None
    for _ in range(5):
        assert runtime.auth.permit_attempt("one-client")
    assert not runtime.auth.permit_attempt("one-client")


async def test_display_state_minimizes_data(runtime):
    runtime.settings.cameras = [Camera(ip="192.168.1.20")]
    state = runtime.display_state()
    assert set(state["cameras"][0]) == {"id", "name"}
    assert "192.168.1.20" not in json.dumps(state)
    assert "settings" not in state


async def test_invalid_layout_and_ptz_are_safe(runtime, client):
    result = await client.post("/api/display", json={"layout": "youtube", "cameras": []})
    assert result.status_code == 422
    camera = Camera(ip="192.168.1.20")
    runtime.settings.cameras = [camera]
    assert (await client.post(f"/api/cameras/{camera.id}/test-move", json={})).status_code == 400
    assert (await client.post("/api/display", json={"cameras": ["missing"]})).status_code == 400






async def test_unknown_media_and_path_traversal_not_proxied(runtime, client):
    assert (await client.get("/media/missing/index.m3u8")).status_code == 404
    assert (await client.get("/static/../secrets.bin")).status_code == 404
    runtime.settings.cameras = [Camera(id="c1", ip="192.168.1.20")]
    assert (await client.get("/media/c1/not-a-file")).status_code == 404




async def test_configuration_permission_error_has_fix(runtime, client, monkeypatch):
    def denied():
        raise PermissionError("sensitive path")

    monkeypatch.setattr(runtime, "save", denied)
    result = await client.post("/api/settings", json={"room_name": "Boardroom"})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "permission"
    assert "sensitive path" not in result.text
