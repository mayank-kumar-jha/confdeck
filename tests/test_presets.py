import xml.etree.ElementTree as ET

import httpx
import pytest

from confdeck.media import video_status
from confdeck.models import Camera
from confdeck.ptz import DEVICE, MEDIA, PTZ, SCHEMA, SOAP, CameraControl


def response(ns, action, body="", status=200):
    return httpx.Response(
        status,
        text=f'<s:Envelope xmlns:s="{SOAP}" xmlns:t="{SCHEMA}"><s:Body><a:{action}Response xmlns:a="{ns}">{body}</a:{action}Response></s:Body></s:Envelope>',
    )


def test_codec_diagnostics_distinguish_unknown_and_hevc():
    assert video_status({})["state"] == "Waiting for camera"
    result = video_status({"codec_name": "hevc", "avg_frame_rate": "60000/1001"})
    assert result["fps"] == 59.9
    assert "HEVC" in result["issue"]["cause"]
    assert video_status({"codec_name": "h264"})["state"] == "Online"


async def test_announced_stream_with_zero_data_is_not_online(runtime, monkeypatch):
    async def api(*args):
        return {"items": [{"name": "silent", "ready": True, "bytesReceived": 0}]}

    monkeypatch.setattr(runtime.media, "api", api)
    await runtime.media.refresh()
    assert runtime.media.statuses["silent"]["state"] == "Waiting for camera"
    assert "has not sent video" in runtime.media.statuses["silent"]["issue"]["detail"]


async def test_canon_list_is_read_only_and_filters_unused_slots():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Livescope-Status": "0"},
            text="p.count:=100\r\nc.1.type:=Canon CR-N100\r\np.1.content:=enabled\r\np.1.name.utf8:=Home\r\np.2.content:=disabled\r\n",
        )

    async with CameraControl(
        Camera(ip="192.168.1.20", control_protocol="canon"), {}, httpx.MockTransport(handle)
    ) as control:
        data = await control.list()
    assert data["presets"] == [{"token": "1", "name": "Home"}]
    assert [r.url.path for r in requests] == ["/-wvhttp-01-/info.cgi"]


@pytest.mark.parametrize(
    "action,params",
    [
        ("save", {"p": "2", "name": "Desk & screen", "ptz": "enabled", "focus": "enabled"}),
        ("recall", {"p": "2", "p.ptzspeed": "15"}),
        ("delete", {"p": "2", "cmd": "delete"}),
        ("stop", {"p.action": "stop"}),
    ],
)
async def test_canon_commands_are_explicit_and_acknowledged(action, params):
    def handle(request):
        assert dict(request.url.params) == params
        return httpx.Response(200, headers={"Livescope-Status": "0"}, text="OK")

    async with CameraControl(
        Camera(ip="192.168.1.20", control_protocol="canon"), {}, httpx.MockTransport(handle)
    ) as control:
        assert (await control.execute(action, "2", "Desk & screen", 15))["ok"]


@pytest.mark.parametrize("headers", [{}, {"Livescope-Status": "403"}])
async def test_canon_rejection_is_not_reported_as_success(headers):
    async with CameraControl(
        Camera(ip="192.168.1.20", control_protocol="canon"),
        {},
        httpx.MockTransport(lambda r: httpx.Response(200, headers=headers)),
    ) as control:
        with pytest.raises(ValueError):
            await control.execute("recall", "1")


async def test_onvif_preset_lifecycle_uses_advertised_profile_and_escapes_values():
    calls = []

    def handle(request):
        root = ET.fromstring(request.content)
        call = list(root.find(f"{{{SOAP}}}Body"))[0]
        action = call.tag.split("}")[-1]
        calls.append(action)
        if action == "GetCapabilities":
            return response(
                DEVICE,
                action,
                "<a:Capabilities><t:Media><t:XAddr>http://192.168.1.20/media</t:XAddr></t:Media><t:PTZ><t:XAddr>http://192.168.1.20/ptz</t:XAddr></t:PTZ></a:Capabilities>",
            )
        if action == "GetProfiles":
            return response(
                MEDIA,
                action,
                '<a:Profiles token="video"/><a:Profiles token="ptz-main"><t:PTZConfiguration/></a:Profiles>',
            )
        assert call.findtext(f"{{{PTZ}}}ProfileToken") == "ptz-main"
        if action == "GetPresets":
            return response(PTZ, action, '<a:Preset token="desk"><t:Name>Desk</t:Name></a:Preset>')
        if action == "SetPreset":
            assert call.findtext(f"{{{PTZ}}}PresetName") == "Desk & <screen>"
            assert call.find(f"{{{PTZ}}}PresetToken") is None
            return response(PTZ, action, "<a:PresetToken>new</a:PresetToken>")
        if action == "Stop":
            assert call.findtext(f"{{{PTZ}}}PanTilt") == "true"
            assert call.findtext(f"{{{PTZ}}}Zoom") == "true"
        else:
            assert call.findtext(f"{{{PTZ}}}PresetToken") == "desk"
        return response(PTZ, action)

    async with CameraControl(
        Camera(ip="192.168.1.20", control_protocol="onvif"), {}, httpx.MockTransport(handle)
    ) as control:
        assert (await control.list())["presets"] == [{"token": "desk", "name": "Desk"}]
        assert (await control.execute("save", name="Desk & <screen>"))["token"] == "new"
        await control.execute("recall", "desk")
        await control.execute("delete", "desk")
        await control.execute("stop")
    assert calls == [
        "GetCapabilities",
        "GetProfiles",
        "GetPresets",
        "SetPreset",
        "GotoPreset",
        "RemovePreset",
        "Stop",
    ]


async def test_onvif_does_not_follow_foreign_service_addresses():
    calls = []

    def handle(request):
        calls.append(request.url)
        return response(
            DEVICE,
            "GetCapabilities",
            "<a:Capabilities><t:Media><t:XAddr>http://192.168.1.99/media</t:XAddr></t:Media><t:PTZ><t:XAddr>http://192.168.1.20/ptz</t:XAddr></t:PTZ></a:Capabilities>",
        )

    async with CameraControl(
        Camera(ip="192.168.1.20", control_protocol="onvif"),
        {"username": "secret-user", "password": "secret-pass"},
        httpx.MockTransport(handle),
    ) as control:
        with pytest.raises(ValueError, match="another host"):
            await control.list()
    assert len(calls) == 1


async def test_onvif_http400_not_authorized_has_useful_message():
    raw = f'<s:Envelope xmlns:s="{SOAP}"><s:Body><s:Fault><s:Code><s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode></s:Code></s:Fault></s:Body></s:Envelope>'
    async with CameraControl(
        Camera(ip="192.168.1.20", control_protocol="onvif"),
        {},
        httpx.MockTransport(lambda r: httpx.Response(400, text=raw)),
    ) as control:
        with pytest.raises(ValueError, match="login"):
            await control.list()


async def test_preset_api_defaults_disabled_and_keeps_tv_isolation(runtime, client):
    c = Camera(ip="192.168.1.20")
    runtime.settings.cameras.append(c)
    assert (await client.get(f"/api/cameras/{c.id}/presets")).status_code == 400
    assert (
        await client.post(f"/api/cameras/{c.id}/presets/recall", json={"token": "1"})
    ).status_code == 400
    result = await client.patch(
        f"/api/cameras/{c.id}", json={"control_protocol": "canon", "control_port": 80}
    )
    assert result.status_code == 200
    assert runtime.settings.cameras[0].control_protocol == "canon"
    assert (
        await client.post(f"/api/cameras/{c.id}/presets/recall", json={"speed": 101})
    ).status_code == 422
