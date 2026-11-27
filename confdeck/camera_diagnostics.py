"""Bounded, read-only RTSP diagnostics. Export only allowlisted, credential-free data."""
import asyncio
import base64
import time
from urllib.parse import quote

import httpx

from confdeck.discovery import tcp_open

CANON_GUIDANCE = (
    "On the camera settings page, configure and enable RTP/RTSP main and substreams. "
    "Choose H.264, 1080p or 720p, 25–30 fps and the shortest I-frame interval. "
    "Prefer config2/config3 for previews. Do not change a stream used by another system. "
    "Camera settings are never changed by this diagnostic."
)
CAUSES = {
    "unreachable": ("RTSP port is unreachable", "Check camera power, IP, RTSP port and the network/firewall."),
    "auth": ("RTSP authentication failed", "Enter the camera's RTSP username and password."),
    "forbidden": ("RTSP access forbidden", "Allow RTSP for this camera account and client IP."),
    "path": ("Wrong RTSP path", "Choose an enabled path; Canon uses /rtpstream/config1, config2 or config3."),
    "limit": ("Camera is busy (another connection may be using this stream)", "Use Share relay for other viewers; check the network and retry."),
    "unconfigured": ("Stream not configured", "Configure and enable the RTP/RTSP stream on the camera settings page."),
    "h265": ("H.265 stream cannot play here", "Select an existing H.264 substream, or configure an unused stream as H.264."),
    "packets": ("No video frames arrived", "Check RTP/firewall rules and camera streaming settings; try TCP transport."),
    "relay": ("Media relay is not receiving video", "Check MediaMTX is running and the selected source is enabled."),
    "tool": ("Video probe tool is missing", "Restore the offline ffprobe bundle in vendor/bin."),
    "keyframe": ("Waiting for keyframe", "Enable RTP streaming and use a 1–2 second I-frame interval on an unused stream. Check firewall rules."),
    "browser": ("Browser has not decoded a frame", "Open this camera preview; test WebRTC, then Use HLS. Check browser playback and local firewall."),
}


def failure(code):
    cause, fix = CAUSES[code]
    return {"code": code, "cause": cause, "fix": fix}


def rtsp_code(status):
    return {401: "auth", 403: "forbidden", 404: "path", 453: "limit", 503: "limit"}.get(status, "unconfigured")


async def describe(camera, credentials):
    """DESCRIBE with Basic/Digest challenge response, including bounded SDP read."""
    uri = f"rtsp://{camera.ip}:{camera.port}{camera.path}"
    request = httpx.Request("DESCRIBE", uri)
    flow = httpx.DigestAuth(credentials.get("username", ""), credentials.get("password", "")).sync_auth_flow(request)
    next(flow)
    authorization = ""
    for attempt in range(2):
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(camera.ip, camera.port, limit=65536), 3)
            headers = f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {attempt+1}\r\nAccept: application/sdp\r\n"
            if authorization:
                headers += f"Authorization: {authorization}\r\n"
            writer.write((headers+"\r\n").encode())
            await writer.drain()
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            lines = raw.decode("latin1").split("\r\n")
            status = int(lines[0].split()[1])
            parsed = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            response = httpx.Response(status, headers={k.strip(): v.strip() for k, v in parsed.items()}, request=request)
            if status == 401 and attempt == 0:
                challenge = response.headers.get("www-authenticate", "")
                if challenge.lower().startswith("basic"):
                    value = credentials.get("username", "")+":"+credentials.get("password", "")
                    authorization = "Basic "+base64.b64encode(value.encode()).decode()
                elif challenge.lower().startswith("digest"):
                    authorization = flow.send(response).headers["authorization"]
                else:
                    return {"status": status, "video": False}
                continue
            size = min(65536, max(0, int(response.headers.get("content-length", 0))))
            body = await asyncio.wait_for(reader.readexactly(size), 3) if size else b""
            return {"status": status, "video": b"m=video " in body}
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()


class CameraDiagnostics:
    def __init__(self, runtime):
        self.runtime = runtime
        self.reports = {}
        self.controls = {}
        self.locks = {}
        # Limit expensive direct ffprobe work to one camera at a time.
        self.probes = asyncio.Semaphore(1)

    async def control(self, camera):
        from confdeck.ptz import CameraControl
        from confdeck.visca import ViscaIpDriver
        try:
            if camera.control_protocol == "visca":
                await ViscaIpDriver(camera).check()
            elif camera.control_protocol != "none":
                async with CameraControl(camera, self.runtime.secrets.get(camera.id)) as control:
                    await control.list()
            else:
                raise ValueError("Choose the camera control protocol first.")
            result = {"state": "OK", "reason": "Read-only control inquiry replied"}
        except (ValueError, OSError, httpx.HTTPError):
            result = {"state": "Not responding", "reason": "Check the selected control protocol, port and camera credentials."}
        result["checked_at"] = time.time()
        self.controls[camera.id] = result
        return result

    async def video(self, camera):
        lock = self.locks.setdefault(camera.id, asyncio.Lock())
        if lock.locked():
            return self.reports.get(camera.id, {"state": "Running"})
        async with lock, self.probes:
            return await self._video(camera)

    async def _video(self, camera):
        from confdeck.media import _probe_stream_direct, binary
        report = {"camera": camera.id, "checked_at": time.time(), "state": "Running", "stages": [], "streams": [], "guidance": CANON_GUIDANCE}
        self.reports[camera.id] = report

        def stage(name, code=None, **details):
            report["stages"].append({"stage": name, "ok": code is None, **details, **(failure(code) if code else {})})
            if code:
                report.update(state="Failed", failure=failure(code))
            return report

        credentials = self.runtime.secrets.get(camera.id)
        media = self.runtime.media
        if media.process and media.process.returncode is None:
            result = await media.probe(camera, credentials)
            report["streams"] = result.get("codec_streams", result.get("codec_autoselect", {}).get("streams", []))
            report["active_path"] = camera.path
            code = (result.get("issue") or {}).get("code")
            if code:
                mapping = {"password": "auth", "path": "path", "limit": "limit", "timeout": "limit", "unreachable": "unreachable", "codec": "h265", "keyframe": "keyframe", "media": "relay"}
                return stage("MediaMTX source / stream", mapping.get(code, "packets"), path=camera.path)
            stage("TCP / DESCRIBE", source="Verified by MediaMTX source receiving a decodable stream; no extra upstream connection")
            stage("Streams", codec=result.get("codec"), width=result.get("width"), height=result.get("height"), fps=result.get("fps"))
        else:
            if not await tcp_open(camera.ip, camera.port):
                return stage("TCP", "unreachable")
            stage("TCP")
            try:
                result = await describe(camera, credentials)
            except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, httpx.HTTPError):
                return stage("DESCRIBE", "limit")
            if result["status"] != 200:
                return stage("DESCRIBE", rtsp_code(result["status"]), status=result["status"])
            if not result["video"]:
                return stage("DESCRIBE", "unconfigured")
            stage("DESCRIBE", status=200)
            executable = binary("ffprobe")
            if not executable:
                return stage("Streams", "tool")
            auth = quote(credentials.get("username", ""), safe="")+":"+quote(credentials.get("password", ""), safe="")+"@" if credentials else ""
            async with media.probe_serial:
                info = await _probe_stream_direct(executable, f"rtsp://{auth}{camera.ip}:{camera.port}{camera.path}")
            report["streams"].append({"path": camera.path, "transport": "tcp", **info})
            if info.get("codec") == "hevc":
                return stage("Streams", "h265")
            if not info.get("frames"):
                return stage("Streams", "keyframe" if info.get("codec") else "packets")
            stage("Streams")
        media = self.runtime.media
        try:
            await media.configure(camera, credentials)
            first = await media.api("GET", f"paths/get/{camera.id}")
            await asyncio.sleep(5)
            last = await media.api("GET", f"paths/get/{camera.id}")
            received = max(0, last.get("bytesReceived", 0)-first.get("bytesReceived", 0))
            details = {"ready": last.get("ready", False), "tracks": last.get("tracks", []), "bytes_received_5s": received, "bitrate_bps": received*8/5}
            if not details["ready"] or not received:
                return stage("MediaMTX", "relay", **details)
            stage("MediaMTX", **details)
        except (OSError, ValueError, httpx.HTTPError):
            return stage("MediaMTX", "relay")
        metric = self.runtime.camera_metrics.get(camera.id, {})
        if not metric.get("frames_received") or time.time()-metric.get("at", 0) > 6:
            return stage("Browser first frame", "browser", transport=metric.get("transport"))
        stage("Browser first frame", transport=metric.get("transport"))
        report["state"] = "Passed"
        return report
