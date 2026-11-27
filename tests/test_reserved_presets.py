from unittest.mock import AsyncMock

import pytest

from confdeck.models import Camera
from confdeck.visca import ViscaIpDriver


@pytest.mark.asyncio
async def test_save_reserved_slot():
    camera = Camera(ip="127.0.0.1", control_enabled=True)
    driver = ViscaIpDriver(camera)
    driver._exchange = AsyncMock()
    await driver.save("21")
    driver._exchange.assert_awaited_once_with(bytes.fromhex("81 01 04 3F 01 15 FF"))
    for invalid in ("20", "30", "-1", "128", "abc"):
        with pytest.raises(ValueError):
            await driver.save(invalid)
    assert driver._exchange.await_count == 1


def test_records_round_trip():
    camera = Camera(ip="127.0.0.1", preset_records={"21": "Desk"})
    assert Camera.model_validate_json(camera.model_dump_json()).preset_records == {"21": "Desk"}
