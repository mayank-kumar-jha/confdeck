import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from confdeck.handoff import Control
from confdeck.models import Camera


def setup(runtime):
    camera = Camera(id="grey", ip="127.0.0.2", control_enabled=True,
                    control_protocol="visca", handoff_mode="live")
    runtime.settings.cameras = [camera]
    cfg = Control(kind="visca_preset", on="80", off="81", settle_ms=100)
    runtime.secrets.set("handoff-grey", ip=camera.ip, config=cfg.model_dump_json(), verified=True)
    return camera, runtime.follow.handoffs


async def test_100_handoffs_with_loss_and_silence(runtime):
    camera, hand = setup(runtime)
    tracking, attempts = False, 0
    async def send(cam, cfg, enabled):
        nonlocal tracking, attempts
        attempts += 1
        tracking = enabled
        if attempts % 13 == 0:
            raise TimeoutError("lost ACK")
    hand.send = send
    async def recall(cid, token):
        assert not tracking, "mock camera ignores recall while tracker is enabled"
        return {"settle_ms": 0}
    await hand.disarm(camera, force=True)
    for turn in range(100):
        token = str(21 + turn % 2)
        await hand.run(camera, token, token, recall, lambda: True, time.monotonic(), asyncio.Event())
        assert tracking and hand.states[camera.id]["state"] == "TRACKING"
    await hand.run(camera, "wide", "1", recall, lambda: True, time.monotonic(), asyncio.Event())
    assert not tracking and hand.states[camera.id]["state"] == "WIDE"
    assert len(hand.samples[camera.id]) == 100


async def test_cancel_before_arm_and_dry_run_no_writes(runtime):
    camera, hand = setup(runtime)
    cancel = asyncio.Event()
    hand.send = AsyncMock()
    recall = AsyncMock(return_value={"settle_ms": None})
    task = asyncio.create_task(hand.run(camera,"seat","21",recall,lambda: not cancel.is_set(),time.monotonic(),cancel))
    while hand.states.get(camera.id, {}).get("state") != "SETTLING":
        await asyncio.sleep(0)
    cancel.set()
    await task
    hand.send.assert_not_awaited()
    camera.handoff_mode="dry"
    recall.reset_mock()
    await hand.run(camera,"seat","21",recall,lambda: True,time.monotonic(),asyncio.Event())
    recall.assert_not_awaited()
    hand.send.assert_not_awaited()


async def test_watchdog_and_protected_presets(runtime):
    from confdeck.visca import ViscaIpDriver
    camera, hand = setup(runtime)
    runtime.settings.tracking.wide_presets = {camera.id:"1"}
    hand.send = AsyncMock()
    recall = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), {"settle_ms":0}])
    with pytest.raises(TimeoutError):
        await hand.run(camera,"seat","21",recall,lambda: True,time.monotonic(),asyncio.Event())
    assert [call.args[1] for call in recall.await_args_list] == ["21","21","1"]
    assert hand.send.await_args.args[-1] is False
    with pytest.raises(ValueError, match="Protected"):
        await ViscaIpDriver(camera).save("80")

async def test_setup_needs_both_physical_observations(runtime,client,monkeypatch):
    camera, hand = setup(runtime)
    camera.handoff_mode = "off"
    path = "/api/cameras/grey/handoff"
    hand.send = AsyncMock()
    monkeypatch.setattr("confdeck.visca.ViscaIpDriver.check", AsyncMock(return_value={"ok":True}))
    async def post(**params):
        return await client.post(path,json=params)
    assert (await post(action="save",config={"kind":"visca_custom","on":"0A 01 32 00 00 02 00","off":"0A 01 32 00 00 03 00"})).status_code == 200
    assert (await post(action="mode",mode="live")).status_code >= 400
    for state in ("on","off"):
        assert (await post(action=state,confirm=True)).status_code == 200
        assert (await post(action="observe",observed=state)).status_code == 200
    assert (await post(action="mode",mode="live")).status_code == 200
    assert (await post(action="pause")).status_code == 200
    assert camera.handoff_mode == "off"


async def test_same_seat_and_manual_disarm(runtime):
    camera, hand = setup(runtime)
    hand.send = AsyncMock()
    recall = AsyncMock(return_value={"settle_ms":0})
    for _ in range(2):
        await hand.run(camera,"A","21",recall,lambda:True,time.monotonic(),asyncio.Event())
    assert recall.await_count==1
    await hand.disarm(camera)
    assert camera.id not in hand.armed
    assert hand.send.await_args.args[-1] is False

async def test_joystick_disarms_before_drive(runtime, monkeypatch):
    camera, hand = setup(runtime)
    hand.armed.add(camera.id)
    actions = []
    async def command(camera, cfg, enabled):
        actions.append("on" if enabled else "off")
    async def drive(driver, *values):
        actions.append("drive")
    hand.send=command
    monkeypatch.setattr("confdeck.visca.ViscaIpDriver.drive",drive)
    monkeypatch.setattr("confdeck.visca.ViscaIpDriver.stop",AsyncMock())
    owner=object()
    await runtime.manual.accept(owner,{"camera":camera.id,"pan":1,"speed":0.2})
    await asyncio.sleep(0)
    await runtime.manual.release(camera.id,owner)
    assert actions[:2] == ["off","drive"]

async def test_handoff_read_restores_fields_without_headers(runtime,client):
    camera, hand = setup(runtime)
    cfg=Control(kind="visca_custom",on="0A 01 32 00 00 02 00",off="0A 01 32 00 00 03 00",headers={"Authorization":"secret-test"})
    runtime.secrets.set("handoff-grey",ip=camera.ip,config=cfg.model_dump_json())
    result=await client.get("/api/cameras/grey/handoff")
    assert result.status_code==200
    assert result.json()["config"]["on"]==cfg.on
    assert "secret-test" not in result.text
    assert "headers" not in result.json()["config"]
    assert result.json()["headers_saved"] is True
