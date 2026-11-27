import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from confdeck.camera_diagnostics import CAUSES, CameraDiagnostics, describe
from confdeck.evaluation.latency import passes_guard
from confdeck.evaluation.metrics import metrics
from confdeck.evaluation.replay import replay
from confdeck.evaluation.synthetic import generate
from confdeck.models import Camera
from confdeck.visca import ViscaIpDriver


@pytest.mark.parametrize("seed", [7, 19, 41])
def test_latency_guard_against_saved_prechange_baseline(seed):
    baseline = json.loads(Path(f"docs/reports/master-baseline-{seed}-new.json").read_text())
    session, settings = generate(seed)
    score = metrics(session, replay(session, settings, "new"), settings)
    assert passes_guard(baseline, score)
    assert score["switch_latency_median"] <= baseline["switch_latency_median"]*.6


@pytest.mark.parametrize("code", list(CAUSES))
async def test_diagnostic_stops_at_first_failure_and_redacts(code, monkeypatch):
    import confdeck.camera_diagnostics as module
    import confdeck.media as media
    camera = Camera(id="test", ip="127.0.0.1")
    runtime = SimpleNamespace(secrets=SimpleNamespace(get=lambda _: {"username": "secret-user", "password": "secret-pass"}), camera_metrics={}, media=SimpleNamespace(process=None, probe_serial=asyncio.Semaphore(1), configure=AsyncMock(), api=AsyncMock(side_effect=[{"bytesReceived": 1}, {"ready": code != "relay", "tracks": ["H264"], "bytesReceived": 101}])))
    monkeypatch.setattr(module, "tcp_open", AsyncMock(return_value=code != "unreachable"))
    statuses = {"auth": 401, "forbidden": 403, "path": 404, "limit": 453}
    monkeypatch.setattr(module, "describe", AsyncMock(return_value={"status": statuses.get(code, 200), "video": code != "unconfigured"}))
    monkeypatch.setattr(media, "binary", lambda _: None if code == "tool" else "mock")
    monkeypatch.setattr(media, "_probe_stream_direct", AsyncMock(return_value={"codec": None if code == "packets" else "hevc" if code == "h265" else "h264", "frames": 0 if code in ("packets", "keyframe") else 1}))
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    report = await CameraDiagnostics(runtime).video(camera)
    assert report["failure"]["code"] == code
    assert sum(not s["ok"] for s in report["stages"]) == 1
    assert not report["stages"][-1]["ok"]
    assert "secret-" not in json.dumps(report)


@pytest.mark.parametrize("challenge", ["Basic realm=\"camera\"", 'Digest realm="camera", nonce="abc", qop="auth", algorithm=MD5'])
async def test_mock_rtsp_authenticated_describe(challenge):
    requests = []
    async def serve(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        requests.append(request)
        if b"Authorization:" not in request:
            writer.write(f"RTSP/1.0 401 Unauthorized\r\nWWW-Authenticate: {challenge}\r\nContent-Length: 0\r\n\r\n".encode())
        else:
            body = b"v=0\r\nm=video 0 RTP/AVP 96\r\n"
            writer.write(f"RTSP/1.0 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode()+body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        camera = Camera(ip="127.0.0.1", port=server.sockets[0].getsockname()[1])
        assert await describe(camera, {"username": "u", "password": "p"}) == {"status": 200, "video": True}
    assert len(requests) == 2
    assert b"Authorization: " in requests[1]


async def test_aver_probe_and_canon_numbering():
    camera = Camera(ip="127.0.0.1", control_enabled=True, control_protocol="visca", model="AVer CAM520 Pro3")
    driver = ViscaIpDriver(camera)
    driver._exchange = AsyncMock(return_value=b"version")
    await driver.check()
    driver._exchange.assert_awaited_once_with(bytes.fromhex("81 09 00 02 FF"), inquiry=True)
    camera.model = "Canon CR-N100"
    driver._exchange.reset_mock()
    await driver.recall("1")
    driver._exchange.assert_awaited_once_with(bytes.fromhex("81 01 04 3F 02 00 FF"))
    assert driver.preset_number("100") == 99


async def test_canon_probe_retries_fixed_local_port():
    camera = Camera(ip="127.0.0.1", model="Canon CR-N100")
    driver = ViscaIpDriver(camera)
    ports = []
    async def exchange(*args, **kwargs):
        ports.append(driver.local_port)
        if not driver.local_port:
            raise ValueError("no reply")
        return b"reply"
    driver._exchange = exchange
    await driver.check()
    assert ports == [0, 0, 0, 52381]
    assert camera.visca_local_port == 52381


async def test_speed_dependent_mock_camera_and_fallback(monkeypatch):
    from confdeck.preset_speed import speed_test
    camera = Camera(ip="127.0.0.1", control_enabled=True, control_protocol="visca", preset_records={"21": "A", "22": "B"})
    loop = asyncio.get_running_loop()
    state = {"current": (0, 0, 0), "target": (0, 0, 0), "arrive": 0, "commands": []}
    positions = {21: (100, 100, 50), 22: (900, 500, 80)}
    async def exchange(self, payload, inquiry=False):
        state["commands"].append(payload)
        if loop.time() >= state["arrive"]:
            state["current"] = state["target"]
        def encode(n):
            return bytes((n >> shift)&15 for shift in (12, 8, 4, 0))
        if inquiry:
            p, t, z = state["current"]
            return b"\x90\x50"+(encode(p)+encode(t) if payload[2:4] == b"\x06\x12" else encode(z))+b"\xff"
        if payload[2:5] == b"\x04\x3f\x02":
            state.update(target=positions[payload[5]], arrive=loop.time()+.1)
        if payload[2:4] == b"\x06\x02":
            def decode(data):
                return sum(n << (4*(3-i)) for i, n in enumerate(data))
            state.update(target=(decode(payload[6:10]), decode(payload[10:14]), state["target"][2]), arrive=loop.time()+1.5/payload[4])
        if payload[2:4] == b"\x04\x47":
            z = sum(n << (4*(3-i)) for i, n in enumerate(payload[4:8]))
            state["target"] = (*state["target"][:2], z)
        return b"\x90\x41\xff"
    monkeypatch.setattr(ViscaIpDriver, "_exchange", exchange)
    result = await speed_test(camera, "21", "22")
    assert result["honored"] and camera.preset_speed_verified
    camera.preset_speeds["22"] = .5
    driver = ViscaIpDriver(camera)
    result = await driver.recall("22")
    assert result["speed_honored"] and result["settle_ms"] is not None
    assert state["commands"][-3][2:5] == b"\x04\x3f\x02" or any(p[2:5] == b"\x04\x3f\x02" for p in state["commands"])
    driver.settled = AsyncMock(side_effect=ValueError("readback mismatch"))
    result = await driver.recall("22")
    assert not result["speed_honored"] and not camera.preset_speed_verified
    assert state["commands"][-1] == bytes.fromhex("81 01 04 3f 02 16 ff")


def test_conversation_requires_distinct_available_cameras():
    from confdeck.conversation import compose
    zones = [SimpleNamespace(id='a', presets={'one':'21','two':'22'}), SimpleNamespace(id='b', presets={'one':'23'})]
    assert compose(zones, ['a','b'], {'one','two'}) == {'two':'22','one':'23'}
    assert compose(zones, ['a','b'], {'one'}) == {}
    assert compose(zones, ['b'], {'one'}) == {'one':'23'}


async def test_speed_test_reports_return_verification_failure(monkeypatch):
    from confdeck.preset_speed import speed_test
    camera = Camera(ip='127.0.0.1', control_enabled=True, control_protocol='visca', preset_records={'21':'A','22':'B'})
    monkeypatch.setattr(ViscaIpDriver, 'position', AsyncMock(return_value=(1,2,3)))
    monkeypatch.setattr(ViscaIpDriver, 'recall', AsyncMock(return_value={'ok':True}))
    monkeypatch.setattr(ViscaIpDriver, 'settled', AsyncMock(side_effect=[{'position':(1,2,3)}, {'position':(10,20,30)}, ValueError('expected (1,2,3), readback (5,6,7)')]))
    monkeypatch.setattr(ViscaIpDriver, 'stop', AsyncMock(side_effect=ValueError('stop rejected')))
    result = await speed_test(camera,'21','22')
    assert result['inconclusive']
    assert result['failed_stage'] == 'return to origin verification'
    assert 'readback' in result['detail']
    assert result['measurements'][-1]['mode'] == 'stop'
    assert not camera.preset_speed_verified


async def test_conversation_keeps_actual_assigned_camera(runtime):
    from confdeck.conversation import compose
    from confdeck.models import Camera
    from confdeck.tracking import TrackingSettings, Zone
    follow = runtime.follow
    runtime.settings.tracking = TrackingSettings(conversation_layout=True, zones=[Zone(id='a', name='A', azimuth=20, elevation=20, presets={'one':'21','two':'22'}), Zone(id='b', name='B', azimuth=200, elevation=20, presets={'one':'23'})])
    runtime.settings.cameras = [Camera(id=cid,ip=ip,control_enabled=True) for cid,ip in [('one','127.0.0.1'),('two','127.0.0.2')]]
    follow.live = True
    follow.recall = AsyncMock(return_value={'ok':True})
    framing = compose(runtime.settings.tracking.zones,['a','b'],{'one','two'})
    await follow.execute({'zone':'a','presets':framing,'display_cameras':list(framing)},follow.generation)
    assert runtime.settings.display.layout == 'two'
    assert runtime.settings.display.cameras == ['two','one']
    follow.update_conversation_display(follow.conversation_changed+6.1)
    assert runtime.settings.display.layout == 'single'
    assert runtime.settings.display.cameras == ['two']
    assert follow.recall.await_count == 2
    follow.stop()
    assert follow.conversation_camera is None
