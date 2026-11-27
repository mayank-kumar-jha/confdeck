import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from confdeck.manual import ManualControl
from confdeck.models import Camera


@pytest.mark.asyncio
async def test_manual_lock_deadman_and_stop(monkeypatch):
    driver = SimpleNamespace(drive=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr("confdeck.manual.ViscaIpDriver", lambda c: driver)
    camera = Camera(ip="127.0.0.1", control_enabled=True, control_protocol="visca")
    runtime = SimpleNamespace(settings=SimpleNamespace(cameras=[camera]), preset_locks={}, follow=Mock(handoffs=SimpleNamespace(armed=set())))
    manual = ManualControl(runtime)
    owner = object()
    data = dict(camera=camera.id, pan=1, tilt=0, zoom=0, speed=.25)
    await manual.accept(owner, data)
    with pytest.raises(ValueError, match="Another controller"):
        await manual.accept(object(), data)
    await asyncio.sleep(.6)
    assert not manual.leases
    driver.stop.assert_awaited_once()
    assert driver.drive.await_count <= 6
    await manual.accept(owner, data)
    await manual.disconnect(owner)
    assert not manual.leases
    assert driver.stop.await_count == 2


@pytest.mark.asyncio
async def test_invalid_control_never_moves():
    manual = ManualControl(SimpleNamespace())
    with pytest.raises(ValueError):
        await manual.accept(object(), dict(pan=float("nan")))


@pytest.mark.asyncio
async def test_drive_error_is_visible_and_stops(monkeypatch):
    driver = SimpleNamespace(drive=AsyncMock(side_effect=ValueError("Camera rejected command")), stop=AsyncMock())
    monkeypatch.setattr("confdeck.manual.ViscaIpDriver", lambda c: driver)
    camera = Camera(ip="127.0.0.1", control_enabled=True, control_protocol="visca")
    runtime = SimpleNamespace(settings=SimpleNamespace(cameras=[camera]), preset_locks={}, follow=Mock(handoffs=SimpleNamespace(armed=set())))
    manual = ManualControl(runtime)
    owner = object()
    await manual.accept(owner, dict(camera=camera.id, pan=1, speed=.25))
    await asyncio.sleep(.1)
    assert manual.errors[owner] == "Camera rejected command"
    driver.stop.assert_awaited_once()
    assert not manual.leases


@pytest.mark.asyncio
async def test_drive_direction_and_zoom_bytes():
    from confdeck.visca import ViscaIpDriver
    driver = ViscaIpDriver(Camera(ip="127.0.0.1"))
    driver._exchange = AsyncMock()
    await driver.drive(1, -1, 1, .5)
    assert driver._exchange.await_args_list[0].args[0] == bytes.fromhex("81 01 06 01 0C 0A 02 02 FF")
    assert driver._exchange.await_args_list[1].args[0] == bytes.fromhex("81 01 04 07 24 FF")
