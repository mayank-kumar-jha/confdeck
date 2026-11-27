import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from confdeck.evaluation.replay import make_engine
from confdeck.microphone import PositionParser
from confdeck.models import MicSettings, Settings
from confdeck.tracking import TrackingSettings, Zone


def configuration(engine="legacy"):
    return TrackingSettings(
        engine=engine, confirm_ms=100, min_hold_ms=500, settle_ms=0,
        ema_alpha=1, level_gate=True,
        zones=[Zone(id="front", name="Front", azimuth=45, elevation=45, presets={"a": "1"}),
               Zone(id="back", name="Back", mic_id="rear", azimuth=45, elevation=45,
                    presets={"b": "2"})],
    )


def event(t, mic="primary", level=-30, **extra):
    return dict(azimuth=45 + (t % .1), elevation=45, sample_time=t,
                mic_id=mic, level=level, activity=True, **extra)


@pytest.mark.parametrize("kind", ["legacy", "new"])
def test_separate_coordinates_conflicting_speech_and_failover(kind):
    engine = make_engine(configuration(kind), kind)
    commands = []
    for i in range(35):
        t = i/10
        for source in ("primary", "rear"):
            result = engine.feed(event(t, source), t)
            if result:
                commands.append(result)
    assert commands == []  # Identical angles from different locations are not one seat.
    assert "disagree" in engine.reason
    for i in range(35, 55):
        t = i/10
        result = engine.feed(event(t, "rear"), t)
        if result:
            commands.append(result)
    assert [c["zone"] for c in commands] == ["back"]
    assert engine.active_mic == "rear"


@pytest.mark.parametrize("kind", ["legacy", "new"])
def test_dominant_source_wins_without_cross_mic_smoothing(kind):
    engine = make_engine(configuration(kind), kind)
    commands = []
    for i in range(30):
        t = i/10
        for source, level in (("primary", -44), ("rear", -30)):
            result = engine.feed(event(t, source, level), t)
            if result:
                commands.append(result)
    assert [c["zone"] for c in commands] == ["back"]


def test_room_move_budget_and_silence_apply_across_mics():
    cfg = configuration()
    cfg.max_moves_per_min = 1
    cfg.wide_presets = {"a": "9"}
    engine = make_engine(cfg)
    for i in range(10):
        engine.feed(event(i/10), i/10)
    assert engine.current == "front"
    for i in range(15, 40):
        engine.feed(event(i/10, "rear"), i/10)
    assert engine.current == "front"
    assert "rate" in engine.reason or "limit" in engine.reason
    assert engine.tick(61)["zone"] == "wide"


def test_freeze_silence_and_unknown_sources_cannot_move():
    engine = make_engine(configuration())
    for i in range(30):
        t = i/10
        assert engine.feed(event(t, "rear", beam_freeze=True), t) is None
        assert engine.feed(event(t, "missing"), t) is None
        assert engine.feed(event(t, level=-85), t) is None
    assert engine.current is None


def test_spherical_duplicates_do_not_erode_posterior():
    cfg = configuration("new")
    cfg.zones = cfg.zones[:1]
    engine = make_engine(cfg, "new")
    for i in range(15):
        engine.feed(event(i/10), i/10)
    before = engine.posterior.copy()
    for i in range(10):
        engine.feed(event(1.4), 1.5+i/10)
    np.testing.assert_array_equal(engine.posterior, before)


def test_old_packet_cannot_replace_level_and_custom_fields_work():
    parser = PositionParser(MicSettings(resource_paths=["/custom/beam"],
                                       field_map={"azimuth": "bearing", "elevation": "tilt"}))
    rows = []
    parser.on_sample = rows.append
    parser.consume({"timestamp": 2, "/custom/beam": {"bearing": 70, "tilt": 40},
                    "/api/audio/inputs/microphone/level": {"peak": -35}}, 2)
    parser.consume({"timestamp": 1, "/custom/beam": {"bearing": 10, "tilt": 10},
                    "/api/audio/inputs/microphone/level": {"peak": -5}}, 3)
    assert len(rows) == 1
    assert rows[0]["azimuth"] == 70
    assert parser.level == -35


async def test_parallel_poll_and_sse_freshness(runtime):
    parser = PositionParser(MicSettings())
    paths = runtime.device_settings.config["telemetry"]
    waiting = set()
    both = asyncio.Event()

    async def read(method, path):
        waiting.add(path)
        if len(waiting) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), .1)  # Sequential reads would time out.
        if path == paths["direction"]:
            parser.consume({paths["direction"]: {"azimuth": 180, "elevation": 45}}, time.monotonic())
            return {"azimuth": 20, "elevation": 45}
        return {"peak": -35}

    mic = SimpleNamespace(parser=parser, resource_request=read)
    runtime.microphones["primary"] = mic
    await runtime.poll_microphone_once("primary", mic)
    assert parser.position["azimuth"] == 180
    assert parser.level == -35
    runtime.microphones.clear()


async def test_multi_api_preserves_identity_and_rejects_duplicate_atomically(runtime, client, monkeypatch):
    monkeypatch.setattr(runtime, "connect_mic", AsyncMock())
    result = await client.post("/api/microphones", json={
        "settings": {"ip": "192.168.1.93", "enabled": True}, "password": "private-mic-secret"})
    assert result.status_code == 200, result.text
    identity = result.json()["id"]
    before = runtime.settings.model_dump()
    duplicate = await client.post("/api/microphones", json={
        "settings": {"ip": "192.168.1.93", "enabled": True}})
    assert duplicate.status_code == 400
    assert runtime.settings.model_dump() == before
    assert "private-mic-secret" not in (await client.get("/api/state")).text
    assert Settings.model_validate_json(runtime.store.backup.read_text()).microphones[0].id == identity
    assert (await client.put(f"/api/microphones/{identity}", json={
        "settings": {"ip": "192.168.1.93", "enabled": False}})).status_code == 200
    assert runtime.settings.microphones[0].id == identity
    assert (await client.delete(f"/api/microphones/{identity}")).status_code == 200
    assert runtime.settings.microphones == []


async def test_reconnect_only_replaces_affected_adapter(runtime):
    runtime.demo = True
    from confdeck.models import AdditionalMic
    primary = MicSettings(enabled=True)
    rear = AdditionalMic(id="rear", ip="192.168.1.93", enabled=True)
    await runtime.connect_mic(primary)
    adapter = runtime.mic
    await runtime.connect_mic(rear, "rear")
    previous = runtime.microphones["rear"]
    await runtime.connect_mic(rear, "rear")
    assert runtime.mic is adapter
    assert runtime.microphones["rear"] is not previous
    assert previous.task.done()
    assert not adapter.task.done()


async def test_device_tuning_targets_selected_microphone(runtime, client):
    from confdeck.models import AdditionalMic
    runtime.settings.microphones = [AdditionalMic(id="rear", ip="192.168.1.93")]
    primary_request = AsyncMock(return_value={"source": "primary"})
    rear_request = AsyncMock(return_value={"source": "rear"})
    runtime.mic = SimpleNamespace(resource_request=primary_request, close=AsyncMock())
    runtime.microphones["rear"] = SimpleNamespace(resource_request=rear_request, close=AsyncMock())
    response = await client.get("/api/mic/device-settings?source=rear")
    assert response.status_code == 200
    assert all(value["source"] == "rear" for value in response.json()["values"].values())
    assert rear_request.await_count > 0
    primary_request.assert_not_awaited()
    assert (await client.get("/api/mic/device-settings?source=missing")).status_code == 400


def test_unmapped_satellite_never_enters_primary_engine():
    cfg = configuration()
    cfg.zones = cfg.zones[:1]
    for kind in ("legacy", "new"):
        tracker = make_engine(cfg, kind)
        for i in range(30):
            assert tracker.feed(event(i/10, "rear"), i/10) is None
        assert tracker.current is None


async def test_selected_camera_stream_survives_reload(runtime):
    from confdeck.models import Camera
    camera = Camera(id="canon", ip="192.168.1.111")
    runtime.settings.cameras = [camera]
    runtime.save()
    selected = camera.model_copy(update={"path": "/rtpstream/config2"})
    runtime.save_camera_source(selected, "/rtpstream/config1")
    assert runtime.store.load().cameras[0].path == "/rtpstream/config2"
    runtime.settings.cameras[0].path = "/operator-selected"
    runtime.save_camera_source(selected, "/rtpstream/config1")
    assert runtime.settings.cameras[0].path == "/operator-selected"


async def test_relay_waits_for_upstream_start(runtime):
    runtime.media.api = AsyncMock(side_effect=[{"ready": False}, {"ready": True}])
    assert await runtime.media.wait_source("camera", timeout=1)
    assert runtime.media.api.await_count == 2

@pytest.mark.parametrize("kind", ["legacy", "new"])
def test_stronger_unconfirmed_candidate_blocks_weaker_confirmed_seat(kind):
    engine = make_engine(configuration(kind), kind)
    # The first source confirms earlier; the other still has valid directional evidence.
    if kind == "legacy":
        engine.sources['rear'].settings.confirm_ms = 2000
    else:
        engine.sources['rear'].cfg.evidence_floor_ms = 2000
    decisions = []
    for i in range(30):
        t = i / 10
        for source, level in (("rear", -25), ("primary", -40)):
            decision = engine.feed(event(t, source, level), t)
            if decision:
                decisions.append(decision['zone'])
    assert decisions == ['back']
    assert engine.current == 'back'


def test_duplicate_does_not_discard_recent_competing_evidence():
    engine = make_engine(configuration())
    for i in range(10):
        t = i / 10
        engine.feed(event(t, 'rear', -25), t)
        engine.feed(event(t, 'primary', -40), t)
    assert engine.current == 'back'
    engine.feed(event(.9, 'rear', -25), 1.0)
    assert 'rear' in engine.votes
    for i in range(10, 15):
        t = i / 10
        assert engine.feed(event(t, 'primary', -40), t) is None
    assert engine.current == 'back'


async def test_tcc2_tcp_telemetry_and_stationary_beam():
    from confdeck.microphone import TCC2Adapter

    requests = []
    async def device(reader, writer):
        try:
            while line := await reader.readline():
                requests.append(line)
                if b'in1' in line:
                    writer.write(b'{"m":{"in1":{"peak":-32}}}\r\n')
                elif b'beam' in line:
                    # Real devices can return separate and fragmented JSON replies.
                    writer.write(b'{"m":{"beam":{"azimuth":45}}}\r\n{"m":{"beam":')
                    await writer.drain()
                    writer.write(b'{"elevation":30}}}\r\n')
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(device, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    adapter = TCC2Adapter(MicSettings(ip='127.0.0.1', port=port, protocol='tcc2'))
    samples = []
    adapter.parser.on_sample = samples.append
    try:
        await adapter.connect()
        for _ in range(30):
            if len(samples) >= 2:
                break
            await asyncio.sleep(.05)
        assert len(samples) >= 2
        assert all((x['azimuth'], x['elevation'], x['level']) == (45, 30, -32) for x in samples)
        assert all(b'null' in r and b'osc' not in r for r in requests)
        assert adapter.status()['protocol'] == 'tcc2'
    finally:
        await adapter.close()
        server.close()
        await server.wait_closed()


def test_multi_mic_speech_gate_refuses_to_start(runtime):
    runtime.settings.tracking = configuration()
    runtime.follow.configure(runtime.settings.tracking)
    runtime.speech_enabled = True
    with pytest.raises(ValueError, match="supports one microphone only"):
        runtime.follow.mode(True)
    assert not runtime.follow.live


async def test_runtime_selects_tcc2_adapter(runtime):
    from confdeck.microphone import TCC2Adapter
    from confdeck.models import AdditionalMic
    settings = AdditionalMic(id="rear", ip="127.0.0.1", port=45,
                             protocol="tcc2", enabled=True)
    runtime.settings.microphones = [settings]
    await runtime.connect_mic(settings, "rear")
    assert isinstance(runtime.microphones["rear"], TCC2Adapter)
