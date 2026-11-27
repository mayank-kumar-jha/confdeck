import asyncio

import pytest

from confdeck.models import Camera
from confdeck.ptz import CameraControl


class ViscaCamera(asyncio.DatagramProtocol):
    def __init__(self):
        self.commands = []
        self.reject = False

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        framed = data[:2] in (b"\x01\x00", b"\x01\x10")
        command = data[8:] if framed else data
        self.commands.append(command)
        if self.reject:
            reply = bytes.fromhex("90 60 02 FF")
        elif command == bytes.fromhex("81 09 00 02 FF"):
            reply = bytes.fromhex("90 50 00 01 00 01 00 01 02 FF")
        else:
            reply = bytes.fromhex("90 41 FF")
        if framed:
            reply = b"\x01\x11" + len(reply).to_bytes(2, "big") + data[4:8] + reply
        self.transport.sendto(reply, addr)


@pytest.fixture
async def visca_camera():
    transport, server = await asyncio.get_running_loop().create_datagram_endpoint(
        ViscaCamera, local_addr=("127.0.0.1", 0)
    )
    camera = Camera(
        ip="127.0.0.1", control_protocol="visca", control_enabled=True,
        visca_port=transport.get_extra_info("sockname")[1],
    )
    yield camera, server
    transport.close()


async def test_visca_preset_connection_is_read_only(visca_camera):
    camera, server = visca_camera
    async with CameraControl(camera, {}) as control:
        data = await control.list()
    assert data == {
        "protocol": "visca", "presets": [], "manual_recall": True, "ok": True, "probe": "Version/position inquiry replied",
    }
    assert server.commands == [bytes.fromhex("81 09 00 02 FF")]


async def test_visca_recall_and_stop_are_acknowledged(visca_camera):
    camera, server = visca_camera
    async with CameraControl(camera, {}) as control:
        assert (await control.execute("recall", "21"))["ok"]
        assert (await control.execute("stop"))["ok"]
    assert server.commands == [
        bytes.fromhex("81 01 04 3F 02 15 FF"),
        bytes.fromhex("81 01 06 01 01 01 03 03 FF"),
        bytes.fromhex("81 01 04 07 00 FF"),
    ]


async def test_visca_disabled_control_and_preset_writes_are_rejected(visca_camera):
    camera, server = visca_camera
    camera.control_enabled = False
    async with CameraControl(camera, {}) as control:
        for action in ("recall", "save", "delete"):
            with pytest.raises(ValueError):
                await control.execute(action, "21")
    assert not server.commands


async def test_visca_camera_rejection_is_not_success(visca_camera):
    camera, server = visca_camera
    server.reject = True
    async with CameraControl(camera, {}) as control:
        with pytest.raises(ValueError, match="rejected"):
            await control.execute("recall", "1")


async def test_visca_preset_routes_use_camera_control(runtime, client, visca_camera):
    camera, server = visca_camera
    runtime.settings.cameras.append(camera)
    base = f"/api/cameras/{camera.id}/presets"
    result = await client.get(base)
    assert result.status_code == 200 and result.json()["manual_recall"]
    assert (await client.post(base + "/recall", json={"token": "21"})).status_code == 200
    assert (await client.post(base + "/stop", json={})).status_code == 200
    assert (await client.post(base + "/save", json={"token": "21"})).status_code == 200
    assert len(server.commands) == 5
    assert camera.preset_records == {"21": ""}
    assert (await client.post(base + "/save", json={"token": "20"})).status_code == 400
