"""Operator-triggered preset controls: Canon XC, ONVIF and VISCA recall.

No discovery, listing, or connection method moves a camera. Credentials stay server-side.
"""

import base64
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import httpx

from confdeck.visca import ViscaIpDriver

SOAP = "http://www.w3.org/2003/05/soap-envelope"
DEVICE = "http://www.onvif.org/ver10/device/wsdl"
MEDIA = "http://www.onvif.org/ver10/media/wsdl"
PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
SCHEMA = "http://www.onvif.org/ver10/schema"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"


class CameraControl:
    def __init__(self, camera, credentials, transport=None):
        if camera.demo or camera.control_protocol == "none":
            raise ValueError(
                "Choose Canon XC, ONVIF or VISCA in the camera's preset control settings first."
            )
        self.camera = camera
        self.credentials = credentials
        self.client = None
        if camera.control_protocol == "visca":
            self.visca = ViscaIpDriver(camera)
            return
        self.base = f"http://{camera.ip}:{camera.control_port}"
        self.client = httpx.AsyncClient(
            auth=httpx.DigestAuth(credentials.get("username", ""), credentials.get("password", "")),
            trust_env=False,
            follow_redirects=False,
            timeout=6,
            transport=transport,
        )
        self.profile = camera.control_profile
        self.endpoint = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    async def request(self, method, url, canon=False, soap_faults=False, **kwargs):
        # Never send camera credentials to a service advertised on another host.
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname != self.camera.ip
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "The camera advertised a control address on another host; it was rejected."
            )
        try:
            async with self.client.stream(method, url, **kwargs) as response:
                if response.status_code in (401, 403):
                    raise ValueError(
                        "Camera control sign-in was rejected. Check the saved camera credentials and control permissions."
                    )
                if response.status_code != 200 and not (
                    soap_faults and response.status_code in (400, 500)
                ):
                    raise ValueError(
                        f"Camera control returned HTTP {response.status_code}. Check the protocol and control port."
                    )
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 512 * 1024:
                        raise ValueError("The camera control response was too large.")
                status = response.headers.get("Livescope-Status")
                if canon and status is None:
                    raise ValueError("The camera did not acknowledge the Canon XC command.")
                if status is not None and (not status.split() or status.split()[0] != "0"):
                    raise ValueError(
                        "Canon rejected the preset command. Check the preset number, camera permissions and current operating mode."
                    )
                return bytes(chunks)
        except httpx.HTTPError:
            raise ValueError(
                "Camera control did not respond. Check its control protocol, port, credentials and network connection."
            ) from None

    async def canon(self, command, params=None):
        raw = await self.request(
            "GET", f"{self.base}/-wvhttp-01-/{command}", params=params or {}, canon=True
        )
        text = raw.decode("utf8", errors="replace")
        if "<html" in text.lower():
            raise ValueError("This address returned a web page instead of Canon XC control data.")
        return dict(re.findall(r"^([^\s=]+?)(?::=|==|=)(.*)$", text, re.MULTILINE))

    def security(self):
        user = self.credentials.get("username", "")
        password = self.credentials.get("password", "")
        if not user:
            return ""
        nonce = os.urandom(20)
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + password.encode()).digest()
        ).decode()
        return (
            f'<wsse:Security xmlns:wsse="{WSSE}" xmlns:wsu="{WSU}">'
            f"<wsse:UsernameToken><wsse:Username>{escape(user)}</wsse:Username>"
            f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>'
            f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
            f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>"
        )

    async def soap(self, url, namespace, action, body=""):
        xml = f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="{SOAP}" xmlns:t="{SCHEMA}"><s:Header>{self.security()}</s:Header><s:Body><a:{action} xmlns:a="{namespace}">{body}</a:{action}></s:Body></s:Envelope>'
        raw = await self.request(
            "POST",
            url,
            content=xml.encode(),
            soap_faults=True,
            headers={
                "Content-Type": "application/soap+xml; charset=utf-8",
                "SOAPAction": f"{namespace}/{action}",
            },
        )
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("Unsafe XML in camera control response.")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise ValueError(
                "The camera did not return an ONVIF response. Check the control protocol and port."
            ) from None
        fault = root.find(f".//{{{SOAP}}}Fault")
        if fault is not None:
            codes = [n.text or "" for n in fault.findall(f".//{{{SOAP}}}Value")]
            if any(c.split(":")[-1] in ("NotAuthorized", "FailedAuthentication") for c in codes):
                raise ValueError(
                    "Camera control login is required or was rejected. Save the camera's ONVIF username/password in Edit camera, and check its clock and control permissions."
                )
            raise ValueError(
                "The camera rejected this ONVIF operation. Check ONVIF permissions, camera time and preset support."
            )
        if root.find(f".//{{{namespace}}}{action}Response") is None:
            raise ValueError("The camera returned an unexpected ONVIF response.")
        return root

    async def connect_onvif(self):
        if self.endpoint:
            return
        root = await self.soap(
            f"{self.base}/onvif/device_service",
            DEVICE,
            "GetCapabilities",
            "<a:Category>All</a:Category>",
        )
        self.endpoint = root.findtext(f".//{{{SCHEMA}}}PTZ/{{{SCHEMA}}}XAddr")
        media = root.findtext(f".//{{{SCHEMA}}}Media/{{{SCHEMA}}}XAddr")
        if not self.endpoint or not media:
            raise ValueError(
                "This camera does not advertise ONVIF Media1 and PTZ services. Use its own control page or a supported protocol."
            )
        profiles = await self.soap(media, MEDIA, "GetProfiles")
        available = [
            p.get("token")
            for p in profiles.findall(f".//{{{MEDIA}}}Profiles")
            if p.find(f"{{{SCHEMA}}}PTZConfiguration") is not None
        ]
        if self.profile and self.profile not in available:
            raise ValueError(
                "The selected ONVIF profile has no PTZ configuration. Clear its profile token to choose automatically."
            )
        if not self.profile:
            self.profile = next(iter(available), "")
        if not self.profile:
            raise ValueError("The camera has no ONVIF profile with PTZ control enabled.")

    async def list(self):
        if self.camera.control_protocol == "visca":
            status = await self.visca.check()
            return {"protocol": "visca", "presets": [{"token": k, "name": v} for k, v in self.camera.preset_records.items()], "manual_recall": True, **status}
        if self.camera.control_protocol == "canon":
            data = await self.canon("info.cgi")
            if "p.count" not in data:
                raise ValueError("Canon did not report preset support for this camera.")
            count = min(100, max(0, int(data["p.count"])))
            return {
                "protocol": "canon",
                "model": data.get("c.1.type", "Canon"),
                "limit": count,
                "presets": [
                    {
                        "token": str(n),
                        "name": data.get(f"p.{n}.name.utf8", "").strip() or f"Preset {n}",
                    }
                    for n in range(1, count + 1)
                    if data.get(f"p.{n}.content", "").strip() == "enabled"
                ],
            }
        await self.connect_onvif()
        root = await self.soap(
            self.endpoint,
            PTZ,
            "GetPresets",
            f"<a:ProfileToken>{escape(self.profile)}</a:ProfileToken>",
        )
        return {
            "protocol": "onvif",
            "profile": self.profile,
            "presets": [
                {"token": p.get("token"), "name": p.findtext(f"{{{SCHEMA}}}Name") or p.get("token")}
                for p in root.findall(f".//{{{PTZ}}}Preset")
                if p.get("token")
            ],
        }

    async def execute(self, action, token="", name="", speed=None):
        if action in ("save", "delete") and str(token).isdigit() and int(token) in self.camera.protected_presets:
            raise ValueError("Protected tracking preset cannot be changed.")
        if action not in ("save", "recall", "delete", "stop"):
            raise ValueError("Unknown preset action.")
        if self.camera.control_protocol == "visca":
            if action == "recall":
                return await self.visca.recall(token)
            if action == "stop":
                return await self.visca.stop()
            if action == "save":
                result = await self.visca.save(token)
                self.camera.preset_records[str(int(token))] = name
                return result
            raise ValueError("Clearing camera presets is unavailable; saved records are retained.")
        if self.camera.control_protocol == "canon":
            if action != "stop" and (
                not token.isascii() or not token.isdigit() or not 1 <= int(token) <= 100
            ):
                raise ValueError("Canon preset numbers must be between 1 and 100.")
            if action == "save":
                # Save position and focus; leave other existing preset groups unchanged.
                await self.canon(
                    "preset/set", {"p": token, "name": name, "ptz": "enabled", "focus": "enabled"}
                )
            elif action == "delete":
                await self.canon("preset/set", {"p": token, "cmd": "delete"})
            elif action == "stop":
                await self.canon("control.cgi", {"p.action": "stop"})
            else:
                params = {"p": token}
                if speed is not None:
                    params["p.ptzspeed"] = str(speed)
                await self.canon("control.cgi", params)
            return {"ok": True, "token": token, "status": "Command accepted by camera"}
        await self.connect_onvif()
        body = f"<a:ProfileToken>{escape(self.profile)}</a:ProfileToken>"
        if action != "stop" and token:
            body += f"<a:PresetToken>{escape(token)}</a:PresetToken>"
        if action in ("delete", "recall") and not token:
            raise ValueError("Choose an existing preset first.")
        # SetPreset schema order is ProfileToken, PresetName, PresetToken.
        if action == "save":
            body = (
                f"<a:ProfileToken>{escape(self.profile)}</a:ProfileToken><a:PresetName>{escape(name)}</a:PresetName>"
                + (f"<a:PresetToken>{escape(token)}</a:PresetToken>" if token else "")
            )
        if action == "stop":
            body += "<a:PanTilt>true</a:PanTilt><a:Zoom>true</a:Zoom>"
        method = {
            "save": "SetPreset",
            "delete": "RemovePreset",
            "recall": "GotoPreset",
            "stop": "Stop",
        }[action]
        result = await self.soap(self.endpoint, PTZ, method, body)
        return {
            "ok": True,
            "token": result.findtext(f".//{{{PTZ}}}PresetToken") or token,
            "status": "Command accepted by camera",
        }
