"""Pass-through MediaMTX supervision; FFprobe reads relay streams, never camera sockets."""

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

from confdeck.discovery import PATHS as CANDIDATE_PATHS
from confdeck.errors import classify_camera, problem
from confdeck.models import Camera
from confdeck.security import atomic_write

log = logging.getLogger("confdeck.media")
ROOT = Path(__file__).resolve().parents[1]

# Per-stream direct-probe timeout (seconds); kept short so all candidates finish fast.
_DIRECT_PROBE_TIMEOUT = 8

# Resolution preference for H.264 auto-selection (width × height, descending).
_PREFERRED_RESOLUTIONS = [(1920, 1080), (1280, 720), (1920, 1200)]


def video_status(stream: dict) -> dict:
    codec = stream.get("codec_name")
    if not codec or codec == "unknown":
        return {
            "state": "Waiting for camera",
            "issue": problem(
                "timeout", "The video codec has not been identified yet. Retry the stream."
            ),
        }
    numerator, denominator = (stream.get("avg_frame_rate") or "0/1").split("/")
    issue = None
    if codec != "h264":
        label = {"hevc": "H.265 / HEVC", "mjpeg": "Motion JPEG"}.get(codec, codec.upper())
        issue = problem("codec")
        issue["cause"] = f"Detected {label}. ConfDeck currently requires H.264 for playback."
        issue["fix"] = (
            "Select an existing H.264 stream in Edit camera, or configure H.264 in the camera's video settings, then Retry. Local H.264 conversion is also available through the transcode_h264 camera setting."
        )
    return {
        "state": "Online" if codec == "h264" else "Unsupported codec",
        "codec": codec,
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": round(float(numerator) / max(1, float(denominator)), 1),
        "issue": issue,
    }


async def _probe_stream_direct(executable: str, url: str, transport="tcp") -> dict:
    """Probe a single RTSP URL directly (no relay).

    Returns a stream-info dict with keys: path, codec, width, height, fps, timed_out.
    Never raises; timeout and errors produce timed_out=True or codec=None.
    """
    process = None
    raw = b""
    errors = b""
    try:
        process = await spawn(
            executable,
            "-v", "error",
            "-rtsp_transport", transport,
            "-rw_timeout", f"{_DIRECT_PROBE_TIMEOUT * 1_000_000}",
            "-i", url,
            "-select_streams", "v:0",
            "-read_intervals", "%+4", "-count_frames",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_read_frames",
            "-of", "json",
            separate_stderr=True,
        )
        raw, errors = await asyncio.wait_for(process.communicate(), _DIRECT_PROBE_TIMEOUT + 2)
        errors = errors or b""
        if process.returncode != 0:
            return {"timed_out": False, "codec": None, "width": None, "height": None, "fps": None, "error": classify_camera((errors+raw).decode(errors="replace"))}
        streams = json.loads(raw).get("streams", [])
        if not streams:
            return {"timed_out": False, "codec": None, "width": None, "height": None, "fps": None, "error": classify_camera(raw.decode(errors="replace"))}
        s = streams[0]
        num, den = (s.get("avg_frame_rate") or "0/1").split("/")
        fps = round(float(num) / max(1, float(den)), 1)
        return {
            "timed_out": False,
            "codec": s.get("codec_name"),
            "width": s.get("width"),
            "height": s.get("height"),
            "fps": fps,
            "frames": int(s.get("nb_read_frames", 0) or 0),
        }
    except asyncio.TimeoutError:
        return {"timed_out": True, "codec": None, "width": None, "height": None, "fps": None}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"timed_out": False, "codec": None, "width": None, "height": None, "fps": None, "error": classify_camera(raw.decode(errors="replace"))}
    finally:
        await terminate(process)


def _score_h264(info: dict) -> int:
    """Higher = better H.264 candidate. Used for auto-selection."""
    w, h = info.get("width") or 0, info.get("height") or 0
    fps = info.get("fps") or 0
    # Prefer preferred resolutions first, then larger area, then fps near 25–30.
    for rank, (pw, ph) in enumerate(_PREFERRED_RESOLUTIONS):
        if w == pw and h == ph:
            res_score = 1000 - rank * 10
            break
    else:
        res_score = w * h // 10000
    fps_score = 5 if 24 <= fps <= 31 else 0
    return res_score + fps_score


async def probe_alternative_streams(
    executable: str, camera: Camera, credentials: dict | None
) -> dict:
    """Probe all candidate paths for *camera* one at a time, directly over TCP.

    Returns:
        {
          "selected": <path str or None>,
          "selected_info": <stream-info dict or None>,
          "streams": [{"path": ..., "codec": ..., "width": ..., "height": ..., "fps": ..., "timed_out": ...}, ...],
        }
    """
    creds = credentials or {}
    auth = ""
    if creds.get("username") or creds.get("password"):
        auth = (
            quote(creds.get("username", ""), safe="")
            + ":"
            + quote(creds.get("password", ""), safe="")
            + "@"
        )

    streams = []
    for path in CANDIDATE_PATHS:
        url = f"rtsp://{auth}{camera.ip}:{camera.port}{path}"
        info = await _probe_stream_direct(executable, url)
        info["path"] = path
        streams.append(info)
        log.debug("alt-probe %s %s → %s", camera.ip, path, info.get("codec") or ("timeout" if info["timed_out"] else "none"))

    # Pick best H.264 stream.
    h264_candidates = [s for s in streams if s.get("codec") == "h264"]
    if h264_candidates:
        best = max(h264_candidates, key=_score_h264)
        return {"selected": best["path"], "selected_info": best, "streams": streams}
    return {"selected": None, "selected_info": None, "streams": streams}


def binary(name: str) -> str | None:
    """Resolve only the bundle or operator PATH; never download executables."""
    filename = name + (".exe" if os.name == "nt" else "")
    for root in (
        Path(os.environ.get("CONFDECK_BIN", ROOT / "vendor" / "bin")),
        Path(__file__).parent / "bin",
    ):
        if (root / filename).is_file():
            return str(root / filename)
    return shutil.which(name)


async def spawn(*args, separate_stderr=False):
    return await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if separate_stderr else asyncio.subprocess.STDOUT,
        **({"creationflags": 0x08000000} if os.name == "nt" else {}),
    )


async def terminate(process) -> None:
    """Bounded graceful termination followed by kill, always reaping the child."""
    if process and process.returncode is None:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), 4)
        except (ProcessLookupError, asyncio.TimeoutError):
            if process.returncode is None:
                process.kill()
                await process.wait()


class Media:
    def __init__(self, root, settings, secrets, display_host="127.0.0.1"):
        self.root, self.settings, self.secrets = root, settings, secrets
        self.display_host = display_host
        self.process = None
        self.task = None
        self.state = "Starting"
        self.issue = None
        self.restarts = 0
        self.statuses = {}
        self.last_errors = {}
        self.config_lock = asyncio.Lock()
        self.probe_locks = {}
        self.probe_serial = asyncio.Semaphore(1)
        self.transports = {}
        self.transcoded = set()
        self.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:9997", trust_env=False, timeout=5
        )
        self.stopping = False
        self.demo_tasks = []
        self.demo_presets = {}
        self.on_failure = None

    def source(self, camera: Camera, credentials: dict | None = None) -> dict:
        if camera.demo:
            return {"source": "publisher"}
        credentials = credentials if credentials is not None else self.secrets.get(camera.id)
        auth = ""
        if credentials.get("username") or credentials.get("password"):
            auth = (
                quote(credentials.get("username", ""), safe="")
                + ":"
                + quote(credentials.get("password", ""), safe="")
                + "@"
            )
        return {
            "source": f"rtsp://{auth}{camera.ip}:{camera.port}{camera.path}",
            "rtspTransport": getattr(self, "transports", {}).get(camera.id, "tcp"),
            "sourceOnDemand": False,
        }

    def relay_paths(self, camera, credentials=None):
        upstream = self.source(camera, credentials)
        if camera.copy_h264 and not camera.transcode_h264 and not camera.demo:
            # FFmpeg negotiates RTSP with Canon before passing the original packets
            # to MediaMTX. Keep the authenticated URL out of the hook command.
            manifest = self.root / f"{camera.id}.ffconcat"
            url = upstream["source"].replace("'", "'\\''")
            data = (f"ffconcat version 1.0\nfile '{url}'\n"
                    "option rtsp_transport tcp\noption timeout 5000000\n").encode()
            if not manifest.exists() or manifest.read_bytes() != data:
                atomic_write(manifest, data)
            encoder = Path(binary("ffmpeg") or "ffmpeg").as_posix()
            command = (
                f'"{encoder}" -nostdin -hide_banner -loglevel quiet '
                f'-protocol_whitelist file,tcp,rtsp,udp,rtp -safe 0 -f concat -i "{manifest.as_posix()}" '
                f'-map 0:v:0 -an -c:v copy -metadata comment={manifest.stat().st_mtime_ns} '
                f'-f rtsp -muxdelay 0 -rtsp_transport tcp rtsp://127.0.0.1:8554/{camera.id}'
            )
            return {camera.id: {"source": "publisher", "runOnInit": command, "runOnInitRestart": True}}
        if not camera.transcode_h264 or camera.demo:
            return {camera.id: upstream}
        raw = f"upstream/{camera.id}"
        encoder = Path(binary("ffmpeg") or "ffmpeg").as_posix()
        base = "rtsp://127.0.0.1:8554"
        upstream.update(
            runOnReady=(
                f'"{encoder}" -nostdin -hide_banner -loglevel error -rtsp_transport tcp '
                f'-i {base}/{raw} -map 0:v:0 -an -vf scale=-2:720 -r 25 '
                f'-c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p '
                f'-g 25 -bf 0 -f rtsp -rtsp_transport tcp {base}/{camera.id}'
            ),
            runOnReadyRestart=True,
        )
        return {camera.id: {"source": "publisher"}, raw: upstream}

    def write_config(self):
        config = {
            "logLevel": "info",
            "logDestinations": ["stdout"],
            "api": True,
            "apiAddress": "127.0.0.1:9997",
            "rtsp": True,
            "rtspAddress": "127.0.0.1:8554" if self.display_host == "127.0.0.1" else "0.0.0.0:8554",
            "rtspTransports": ["tcp"],
            "rtmp": False,
            "srt": False,
            "moq": False,
            "playback": False,
            "hls": True,
            "hlsAddress": "127.0.0.1:8888",
            "hlsAlwaysRemux": False,
            "hlsVariant": "lowLatency",
            "hlsSegmentDuration": "1s",
            "hlsPartDuration": "200ms",
            "webrtc": True,
            "webrtcAddress": "127.0.0.1:8889",
            "webrtcLocalUDPAddress": f"{self.display_host}:8189",
            "webrtcLocalTCPAddress": f"{self.display_host}:8189",
            "webrtcIPsFromInterfaces": self.display_host != "127.0.0.1",
            "webrtcAdditionalHosts": ["127.0.0.1"]
            + ([self.display_host] if self.display_host != "127.0.0.1" else []),
            "webrtcICEServers2": [],
            "metrics": False,
            "pprof": False,
            "authInternalUsers": ([{"user": "any", "ips": [str(__import__("ipaddress").ip_network(self.display_host+"/24", strict=False))], "permissions": [{"action": "read"}]}] if self.display_host != "127.0.0.1" else []) + [
                {
                    "user": "any",
                    "ips": ["127.0.0.1", "::1"],
                    "permissions": [{"action": a} for a in ("api", "read", "publish")],
                }
            ],
            "pathDefaults": {"record": False},
            "paths": {name: body for c in self.settings.cameras for name, body in self.relay_paths(c).items()},
        }
        atomic_write(self.root / "mediamtx.yml", yaml.safe_dump(config).encode())
        self.transcoded = {c.id for c in self.settings.cameras if c.transcode_h264 and not c.demo}

    async def start(self):
        self.task = asyncio.create_task(self.supervise())

    async def supervise(self):
        delay = 1
        while not self.stopping:
            executable = binary("mediamtx")
            if not executable:
                self.state, self.issue = (
                    "Unavailable",
                    problem("media", "MediaMTX is missing from vendor/bin."),
                )
                await asyncio.sleep(10)
                continue
            self.write_config()
            started = time.monotonic()
            try:
                self.process = await spawn(executable, str(self.root / "mediamtx.yml"))
                self.state = "Starting"
                self.issue = None
                async for line in self.process.stdout:
                    message = line.decode(errors="replace").strip()
                    log.info(message)
                    if "listener opened" in message or "started with listener" in message:
                        self.state = "Connected"
                    if "ERR" in message or "error" in message.lower():
                        identities = {c.id for c in self.settings.cameras} | set(self.probe_locks)
                        for identity in identities:
                            if identity in message:
                                self.last_errors[identity] = classify_camera(message)
                    if "address already in use" in message or "Only one usage" in message:
                        self.issue = problem(
                            "media",
                            "A video port is already in use. Close another relay or app instance.",
                        )
                await self.process.wait()
            except (OSError, PermissionError):
                self.issue = problem(
                    "media", "The bundled media relay could not start. Check file permissions."
                )
            finally:
                await terminate(self.process)
            if self.stopping:
                break
            self.state = "Reconnecting"
            self.issue = self.issue or problem("media")
            self.restarts += 1
            if time.monotonic() - started > 30:
                delay = 1
            await asyncio.sleep(delay)
            delay = min(30, delay * 2)

    async def api(self, method: str, path: str, **kwargs):
        response = await self.client.request(method, "/v3/" + path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    async def configure(self, camera: Camera, credentials=None):
        async with self.config_lock:
            paths = self.relay_paths(camera, credentials)
            for name, body in paths.items():
                current = await self.client.get(f"/v3/config/paths/get/{name}")
                if current.status_code == 200:
                    if current.json().get("runOnInit") and "runOnInit" not in body:
                        body.update(runOnInit="", runOnInitRestart=False)
                    if all(current.json().get(k) == v for k, v in body.items()):
                        continue
                    response = await self.client.patch(f"/v3/config/paths/patch/{name}", json=body)
                else:
                    response = await self.client.post(f"/v3/config/paths/add/{name}", json=body)
                response.raise_for_status()
            if not camera.copy_h264 or camera.transcode_h264:
                (self.root / f"{camera.id}.ffconcat").unlink(missing_ok=True)
            if camera.transcode_h264 and not camera.demo:
                self.transcoded.add(camera.id)
            elif camera.id in self.transcoded:
                response = await self.client.delete(f"/v3/config/paths/delete/upstream/{camera.id}")
                if response.status_code != 404:
                    response.raise_for_status()
                self.transcoded.discard(camera.id)

    async def remove(self, identity: str):
        async with self.config_lock:
            try:
                if identity in self.transcoded:
                    await self.client.delete(f"/v3/config/paths/delete/upstream/{identity}")
                    self.transcoded.discard(identity)
                await self.client.delete(f"/v3/config/paths/delete/{identity}")
            except httpx.TransportError:
                pass
        (self.root / f"{identity}.ffconcat").unlink(missing_ok=True)
        self.statuses.pop(identity, None)
        self.last_errors.pop(identity, None)
        self.probe_locks.pop(identity, None)

    async def probe(self, camera: Camera, credentials=None) -> dict:
        lock = self.probe_locks.setdefault(camera.id, asyncio.Lock())
        async with self.probe_serial, lock:
            executable = binary("ffprobe")
            if not executable:
                return {"state": "Needs probe tool", "issue": problem("media")}
            original = camera.path
            identity = (camera.name+camera.model).lower()
            canon = "aver" not in identity and (camera.path.startswith("/rtpstream/config") or "canon" in identity)
            paths = list(dict.fromkeys([original, *(["/rtpstream/config2", "/rtpstream/config3", "/rtpstream/config1"] if canon and not camera.transcode_h264 and not camera.copy_h264 else [])]))
            attempts = []
            first_result = None
            result = None
            try:
                for index, path in enumerate(paths):
                    candidate = camera.model_copy(update={"path": path})
                    if index:
                        await asyncio.sleep(min(2, .5*2**(index-1)))
                    for transport in (["tcp", "udp"] if canon and not camera.transcode_h264 and not camera.copy_h264 else ["tcp"]):
                        self.transports[camera.id] = transport
                        self.last_errors.pop(camera.id, None)
                        await self.configure(candidate, credentials)
                        # A relay path can return 404 while its new upstream is still
                        # connecting. Do not confuse that with a missing camera path.
                        if self.process and self.process.returncode is None:
                            await self.wait_source(camera.id)
                        # Only MediaMTX owns upstream sessions, including fallback paths.
                        info = await _probe_stream_direct(executable, f"rtsp://127.0.0.1:8554/{camera.id}")
                        attempts.append({"path": path, "transport": transport, **info})
                        if info.get("codec"):
                            result = {"codec": info["codec"], "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps")}
                            if info["codec"] != "h264":
                                result.update(state="Unsupported codec", issue=problem("codec"))
                            elif not info.get("frames"):
                                result.update(state="Waiting for keyframe", issue={"code": "keyframe", "title": "Waiting for keyframe", "cause": "H.264 was advertised but no decodable frame arrived.", "fix": "Enable RTP output and use a 1–2 second I-frame interval on an unused stream. Check the camera streaming settings and firewall."})
                            else:
                                camera.path = path
                                result.update(state="Online", issue=None, active_path=path, source_transport=transport)
                                if path != original:
                                    result["codec_autoselect"] = {"chosen_path": path, "streams": attempts}
                                    callback = getattr(self, "on_source_selected", None)
                                    if callback:
                                        callback(camera, original)
                                break
                        else:
                            code = self.last_errors.get(camera.id, info.get("error", "limit"))
                            result = {"state": problem(code)["title"], "issue": problem(code)}
                        if first_result is None:
                            first_result = result
                        if not info.get("timed_out"):
                            break
                    if result.get("state") == "Online" or result.get("issue", {}).get("code") == "password":
                        break
                if result.get("state") != "Online":
                    self.transports[camera.id] = "tcp"
                    await self.configure(camera.model_copy(update={"path": original}), credentials)
                    result = first_result or result
                    result["codec_streams"] = attempts
            except (httpx.HTTPError, OSError, ValueError):
                result = {"state": "Waiting for relay", "issue": problem("media")}
            result.update(checked_at=time.time(), active_path=camera.path)
            self.statuses[camera.id] = result
            if self.on_failure and result.get("state") != "Online":
                self.on_failure(camera)
            return result

    async def wait_source(self, identity, timeout=6):
        deadline = time.monotonic() + timeout
        await asyncio.sleep(.1)
        while time.monotonic() < deadline:
            try:
                if (await self.api("GET", f"paths/get/{identity}")).get("ready"):
                    return True
            except (httpx.HTTPError, ValueError):
                pass
            if self.last_errors.get(identity) == "password":
                return False
            await asyncio.sleep(.1)
        return False

    async def refresh(self):
        try:
            paths = await self.api("GET", "paths/list")
            if self.process and self.process.returncode is None:
                self.state = "Connected"
            for path in paths.get("items", []):
                identity = path["name"]
                if identity.startswith("upstream/"):
                    continue
                status = self.statuses.setdefault(identity, {})
                tracks = path.get("tracks", [])
                if path.get("ready") and "H265" in tracks and "H264" not in tracks:
                    status.update(state="Unsupported codec", codec="hevc", issue=problem("codec"))
                    continue
                if status.get("state") in ("Unsupported codec", "Waiting for keyframe"):
                    continue
                if path.get("ready"):
                    if path.get("bytesReceived") == 0:
                        status.update(
                            state="Waiting for camera",
                            issue=problem(
                                "timeout",
                                "The camera announced a stream but has not sent video data.",
                            ),
                        )
                    else:
                        status.update(state="Online", issue=None, last_data=time.time())
                elif status.get("state") == "Online":
                    code = self.last_errors.get(identity, "timeout")
                    status.update(state="Waiting for camera", issue=problem(code))
        except (httpx.HTTPError, ValueError, KeyError):
            pass

    async def demo_camera(self, camera):
        executable = binary("ffmpeg")
        if not executable:
            return
        process = None
        try:
            while True:
                if self.state != "Connected":
                    await asyncio.sleep(1)
                    continue
                pattern = f"testsrc2=size=640x360:rate=15,drawbox=x={int(camera.id[-1]) * 70}:y=40:w=100:h=80:color=orange:t=fill"
                process = await spawn(
                    executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-re",
                    "-f",
                    "lavfi",
                    "-i",
                    pattern,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-tune",
                    "zerolatency",
                    "-profile:v",
                    "baseline",
                    "-pix_fmt",
                    "yuv420p",
                    "-g",
                    "15",
                    "-bf",
                    "0",
                    "-threads",
                    "1",
                    "-f",
                    "rtsp",
                    "-rtsp_transport",
                    "tcp",
                    f"rtsp://127.0.0.1:8554/{camera.id}",
                )
                await process.communicate()
                await asyncio.sleep(2)
        finally:
            await terminate(process)

    async def close(self):
        self.stopping = True
        for task in self.demo_tasks:
            task.cancel()
        await asyncio.gather(*self.demo_tasks, return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await terminate(self.process)
        await self.client.aclose()
