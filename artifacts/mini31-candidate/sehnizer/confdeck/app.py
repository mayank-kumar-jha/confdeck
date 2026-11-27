"""Two isolated HTTP applications: localhost administration and paired displays."""

import asyncio
import contextlib
import io
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
import qrcode
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, SecretStr, ValidationError
from starlette.background import BackgroundTask

from confdeck.errors import problem
from confdeck.models import AdditionalMic, Camera, DisplaySettings, MicSettings, Settings
from confdeck.ptz import CameraControl
from confdeck.security import SECRET_VALUES
from confdeck.tracking_api import add_tracking_routes

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("confdeck.api")


class Pair(BaseModel):
    code: str = Field(max_length=16)
    name: str = Field(default="Room display", min_length=1, max_length=80)


class CameraInput(BaseModel):
    name: str = Field(default="Meeting room camera", min_length=1, max_length=80)
    ip: str
    port: int = 554
    path: str = "/rtpstream/config1"
    username: str = Field(default="", max_length=128)
    password: SecretStr = SecretStr("")
    use_scan_credentials: bool = False
    control_protocol: Literal["none", "canon", "onvif", "visca"] = "none"
    control_enabled: bool = False
    visca_port: int = Field(default=52381, ge=1, le=65535)


class PresetAction(BaseModel):
    token: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=80)
    speed: int | None = Field(default=None, ge=1, le=100)


class ScanInput(BaseModel):
    subnet: str
    username: str = Field(default="", max_length=128)
    password: SecretStr = SecretStr("")


class MicInput(BaseModel):
    settings: MicSettings
    password: SecretStr | None = None


class Metrics(BaseModel):
    camera: str | None = Field(default=None, max_length=40)
    fps: float | None = Field(default=None, ge=0, le=300)
    latency_ms: float | None = Field(default=None, ge=0, le=120000)
    transport: str = Field(default="", max_length=20)
    note: str = Field(default="", max_length=200)
    frames_received: bool = False


def create_app(runtime, display=False, lifecycle=False) -> FastAPI:
    """Create a listener-specific app. The LAN app never mounts administrative routes."""

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if lifecycle:
            await runtime.start()
        yield
        if lifecycle:
            await runtime.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.runtime = runtime

    def identity(connection):
        if display:
            return runtime.auth.display(connection.cookies.get("confdeck_display", ""))
        # Temporarily PIN-free. The admin listener and Host/Origin checks remain local-only.
        return "admin"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.url.hostname
        if not display and host not in ("127.0.0.1", "localhost", "testserver", runtime.display_host):
            is_private = False
            try:
                import ipaddress
                is_private = ipaddress.ip_address(host).is_private
            except Exception:
                pass
            if not is_private:
                return JSONResponse(
                    {"error": problem("invalid", "Administration is available on local network only.")},
                    status_code=403,
                )
        if display and host not in (runtime.display_host, "127.0.0.1", "localhost", "testserver"):
            return JSONResponse(
                {"error": problem("invalid", "Use the display URL shown by ConfDeck.")},
                status_code=403,
            )
        path = request.url.path
        public = path in (
            ("/display", "/pair") if display else ("/", "/api/login")
        ) or path.startswith("/static/")
        if not public and not identity(request):
            return JSONResponse(
                {
                    "error": {
                        "title": "Enter the display pairing code"
                        if display
                        else "Enter the startup PIN",
                        "cause": "This session is not signed in.",
                        "fix": "Use the code shown in ConfDeck.",
                    }
                },
                status_code=401,
            )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if (origin and origin != expected) or request.headers.get("x-confdeck") != "1":
                return JSONResponse(
                    {"error": problem("invalid", "Reload ConfDeck and retry this action.")},
                    status_code=403,
                )
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                size = 2**40
            limit = 4_000_000 if (path == "/api/tracking/replay" or path.startswith("/api/accuracy/")) else 128 * 1024
            if size > limit:
                return JSONResponse(
                    {"error": problem("invalid", "The submitted file or message is too large.")},
                    status_code=413,
                )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not serialize Pydantic's `input`: it can contain camera passwords.
        return JSONResponse(
            {
                "error": problem("invalid"),
                "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
            },
            status_code=422,
        )

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        if str(exc).startswith("Camera rejected VISCA command"):
            return JSONResponse({"error": {
                "code": "camera_command_rejected", "title": "Camera rejected the command",
                "detail": str(exc)[:240],
                "cause": "The camera returned a VISCA error response.",
                "fix": "This command has not been verified for this camera. Keep live handoff off."
            }}, status_code=400)
        detail = "Review the device address, stream path and settings."
        if not isinstance(exc, ValidationError):
            detail = str(exc)[:240]
        return JSONResponse({"error": problem("invalid", detail)}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(
            {"error": problem("invalid", str(exc.detail))}, status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        code = (
            "permission"
            if isinstance(exc, PermissionError)
            else "media"
            if isinstance(exc, httpx.HTTPError)
            else "internal"
        )
        log.warning("Action failed (%s): %s", request.url.path, type(exc).__name__)
        return JSONResponse({"error": problem(code)}, status_code=503)

    @app.get("/static/{asset:path}")
    async def static_asset(asset: str):
        allowed = {
            "style.css",
            "common.js",
            "player.js",
            "display.js",
            "vendor/hls.min.js",
            "vendor/inter.woff2",
        }
        if not display:
            allowed.update({"app.js", "accuracy-lab.js", "studio.js", "smart-preview.js", "mic-tuning.js", "seat-radar.js", "tune.js", "tracker.css", "ordain-logo.png", "ordain-logo-light.png"})
        if asset not in allowed:
            raise HTTPException(404, "File not found")
        return FileResponse(STATIC / asset)

    @app.api_route(
        "/media/{camera_id}/{resource:path}", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
    )
    async def media_proxy(camera_id: str, resource: str, request: Request):
        camera = next((c for c in runtime.settings.cameras if c.id == camera_id), None)
        if not camera:
            raise HTTPException(404, "Camera not found")
        if display and camera_id not in {c["id"] for c in runtime.display_state()["cameras"]}:
            raise HTTPException(403, "This camera is not selected for the display")
        if runtime.media.statuses.get(camera_id, {}).get("state") == "Unsupported codec":
            return JSONResponse({"error": problem("codec")}, status_code=415)
        if (
            not re.fullmatch(
                r"(?:whep(?:/[a-zA-Z0-9_-]+)?|[a-zA-Z0-9_.-]+\.(?:m3u8|mp4|mp|m4s|ts))", resource
            )
            or ".." in resource
        ):
            raise HTTPException(404, "Video resource not found")
        is_whep = resource.startswith("whep")
        if not is_whep and request.method != "GET":
            raise HTTPException(405, "Only viewing is available")
        owner = identity(request)
        key = (camera_id, resource)
        if is_whep and "/" in resource and runtime.whep_sessions.get(key, (None,))[0] != owner:
            raise HTTPException(403, "This video session belongs to another viewer")
        port = 8889 if is_whep else 8888
        parameters = {
            k: v
            for k, v in request.query_params.items()
            if k in ("_HLS_msn", "_HLS_part", "_HLS_skip", "session")
            and re.fullmatch(r"[0-9A-Za-z-]{1,100}", v)
        }
        if not is_whep and resource == "index.m3u8":
            parameters["cookieCheck"] = "1"
        client = runtime.relay_client
        upstream = None
        try:
            body = await request.body()
            if len(body) > 65536:
                raise HTTPException(413, "Video request too large")
            headers = {
                k: request.headers[k]
                for k in ("content-type", "if-match", "range")
                if k in request.headers
            }
            upstream = await client.send(
                client.build_request(
                    request.method,
                    f"http://127.0.0.1:{port}/{camera_id}/{resource}",
                    params=parameters,
                    headers=headers,
                    content=body,
                ),
                stream=True,
            )
            outgoing = {
                k: upstream.headers[k]
                for k in ("content-type", "etag", "accept-patch", "content-range")
                if k in upstream.headers
            }
            if is_whep and "location" in upstream.headers:
                location = urlsplit(upstream.headers["location"]).path
                prefix = f"/{camera_id}/"
                if not location.startswith(prefix) or not re.fullmatch(
                    r"whep/[a-zA-Z0-9_-]+", location[len(prefix) :]
                ):
                    raise ValueError("The video relay returned an invalid session.")
                session_resource = location[len(prefix) :]
                outgoing["location"] = "/media" + location
                runtime.whep_sessions[(camera_id, session_resource)] = (owner, time.monotonic())
                runtime.whep_sessions = {
                    k: v
                    for k, v in runtime.whep_sessions.items()
                    if time.monotonic() - v[1] < 86400
                }
                if len(runtime.whep_sessions) > 2048:
                    runtime.whep_sessions.pop(next(iter(runtime.whep_sessions)))
            if request.method == "DELETE":
                runtime.whep_sessions.pop(key, None)

            async def cleanup():
                await upstream.aclose()

            return StreamingResponse(
                upstream.aiter_bytes(),
                status_code=upstream.status_code,
                headers=outgoing,
                background=BackgroundTask(cleanup),
            )
        except Exception:
            if upstream:
                await upstream.aclose()
            raise

    async def websocket_loop(ws: WebSocket):
        origin = ws.headers.get("origin")
        if origin != f"http://{ws.headers.get('host')}" or not identity(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        async def receive_controls():
            try:
                while identity(ws):
                    data = await ws.receive_json()
                    try:
                        await runtime.manual.accept(ws, data)
                    except (ValueError, TypeError) as exc:
                        await runtime.manual.disconnect(ws)
                        runtime.manual.errors[ws] = str(exc)
            finally:
                await runtime.manual.disconnect(ws)
        receiver = asyncio.create_task(receive_controls()) if not display else None
        try:
            while identity(ws):
                state = runtime.display_state() if display else runtime.snapshot()
                if not display:
                    # Raw viewer is fetched on demand; don't retransmit 200 messages at 5 Hz.
                    state["mic"].pop("raw", None)
                    state["mic"].pop("history", None)
                if not display:
                    state["manual_cameras"] = list(runtime.manual.leases)
                    state["manual_error"] = runtime.manual.errors.pop(ws, None)
                await asyncio.wait_for(ws.send_json(state), 5)
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
            pass

        finally:
            if receiver:
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await receiver
                await runtime.manual.disconnect(ws)
                runtime.manual.errors.pop(ws, None)

    app.websocket("/view/ws" if display else "/api/ws")(websocket_loop)

    if display:

        @app.get("/display")
        async def display_page():
            return FileResponse(STATIC / "display.html")

        @app.post("/pair")
        async def pair(body: Pair, request: Request):
            if not runtime.auth.permit_attempt("display:" + request.client.host):
                raise HTTPException(
                    429, "Too many tries. Wait one minute and enter the current pairing code."
                )
            token = runtime.auth.pair(body.code, body.name)
            if not token:
                raise HTTPException(
                    401,
                    "That pairing code is wrong or has expired. Click Connect to TV in ConfDeck.",
                )
            response = JSONResponse({"ok": True})
            response.set_cookie(
                "confdeck_display", token, httponly=True, samesite="strict", max_age=30 * 86400
            )
            return response

        @app.get("/view/state")
        async def view_state():
            return runtime.display_state()

        @app.post("/view/heartbeat")
        async def heartbeat(body: Metrics, request: Request):
            runtime.display_seen[identity(request)] = {
                "last_seen": time.time(),
                "latency_ms": body.latency_ms,
                "fps": body.fps,
                "transport": body.transport,
                "note": body.note,
            }
            return {"ok": True}

        return app

    add_tracking_routes(app, runtime)
    from confdeck.evaluation.api import add_accuracy_routes
    add_accuracy_routes(app, runtime)

    @app.get("/")
    async def admin_page():
        return FileResponse(STATIC / "index.html")

    @app.post("/api/login")
    async def login():
        # Compatibility for already-open clients; no PIN or cookie is required.
        return {"ok": True}

    @app.get("/api/state")
    async def state():
        return runtime.snapshot()

    @app.get('/api/far-end')
    async def far_end_state():
        from confdeck.far_end import inputs
        return {**runtime.far_end.snapshot(), 'inputs': inputs()}

    @app.put('/api/far-end')
    async def far_end_settings(body: dict):
        await asyncio.to_thread(runtime.far_end.configure, body)
        return runtime.far_end.snapshot()

    @app.post('/api/far-end/test')
    async def far_end_test(body: dict):
        return await asyncio.to_thread(runtime.far_end.test_input, body)

    @app.post("/api/logout")
    async def logout(request: Request):
        runtime.auth.admin_sessions.pop(request.cookies.get("confdeck_admin", ""), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("confdeck_admin")
        return response

    @app.post("/api/system/stop")
    async def stop_system():
        if runtime.request_shutdown:
            asyncio.get_running_loop().call_later(0.5, runtime.request_shutdown)
        return {"ok": True}

    @app.post("/api/system/reboot")
    async def reboot_system(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        hardware = bool(body and body.get("hardware"))
        log.info("System reboot requested (hardware=%s)", hardware)

        loop = asyncio.get_running_loop()

        if hardware:
            def trigger_hw():
                try:
                    subprocess.Popen(["sudo", "reboot"])
                except Exception as e:
                    log.error("Failed to trigger hardware reboot: %s", e)

            loop.call_later(0.5, trigger_hw)
            return {"ok": True, "action": "hardware_reboot", "message": "Mini PC hardware reboot initiated."}

        def trigger_restart():
            # Check if running under systemd
            is_systemd = False
            try:
                res = subprocess.run(["systemctl", "is-active", "confdeck"], capture_output=True, text=True)
                if res.returncode == 0 and "active" in res.stdout:
                    is_systemd = True
            except Exception:
                pass

            if is_systemd:
                log.info("Restarting confdeck service via systemctl...")
                try:
                    subprocess.Popen(["sudo", "systemctl", "restart", "confdeck"])
                    return
                except Exception as e:
                    log.error("Failed systemctl restart: %s", e)

            # In-process runtime reboot fallback
            asyncio.run_coroutine_threadsafe(runtime.reboot(), loop)

        loop.call_later(0.5, trigger_restart)
        return {"ok": True, "action": "service_reboot", "message": "Room system reboot initiated."}

    @app.post("/api/settings")
    async def settings(body: dict):
        if not set(body) <= {"room_name", "wizard_done", "preview_limit"}:
            raise ValueError("Use the relevant device page to change these settings.")
        runtime.settings = Settings.model_validate({**runtime.settings.model_dump(), **body})
        runtime.save()
        return {"ok": True}

    @app.post("/api/scan")
    async def scan(body: ScanInput):
        SECRET_VALUES.add(body.password.get_secret_value())
        await runtime.scanner.start(body.subnet, body.username, body.password.get_secret_value())
        return {"ok": True}

    @app.post("/api/scan/cancel")
    async def cancel_scan():
        await runtime.scanner.close()
        return {"ok": True}

    def find_camera(identity):
        camera = next((c for c in runtime.settings.cameras if c.id == identity), None)
        if not camera:
            raise HTTPException(404, "Camera not found")
        return camera

    @app.post("/api/cameras")
    async def add_camera(body: CameraInput):
        camera = Camera(**body.model_dump(exclude={"username", "password", "use_scan_credentials"}))
        async with runtime.write_lock:
            existing = next(
                (
                    c
                    for c in runtime.settings.cameras
                    if c.ip == camera.ip and c.port == camera.port
                ),
                None,
            )
            if existing:
                return {"id": existing.id, "already_added": True}
            if len(runtime.settings.cameras) >= 16:
                raise ValueError(
                    "ConfDeck supports up to 16 saved cameras and four simultaneous previews."
                )
            creds = (
                runtime.scanner.credentials
                if body.use_scan_credentials
                else {"username": body.username, "password": body.password.get_secret_value()}
            )
            runtime.secrets.set(camera.id, **creds)
            runtime.settings.cameras.append(camera)
            if len(runtime.settings.display.cameras) < 4:
                runtime.settings.display.cameras.append(camera.id)
            runtime.save()
        runtime.background(runtime.media.probe(camera))
        return {"id": camera.id}

    @app.patch("/api/cameras/{identity}")
    async def edit_camera(identity: str, body: dict):
        runtime.follow.stop("Camera settings changed")
        allowed = {
            "name",
            "ip",
            "port",
            "path",
            "username",
            "password",
            "control_protocol",
            "control_port",
            "control_profile",
            "control_enabled",
            "visca_port",
            "transcode_h264",
            "copy_h264",
        }
        if not set(body) <= allowed:
            raise ValueError("Unknown camera setting.")
        async with runtime.write_lock:
            camera = find_camera(identity)
            if runtime.follow.handoffs.busy(identity) or camera.handoff_mode == "live":
                raise ValueError("Pause tracking handoff before changing camera settings.")
            if camera.native_tracking and body.get("control_enabled"):
                raise ValueError("Turn off camera-native tracking before enabling preset or joystick control.")
            updated = Camera.model_validate(
                {
                    **camera.model_dump(),
                    **{k: v for k, v in body.items() if k not in ("username", "password")},
                }
            )
            data = runtime.settings.model_dump()
            data["cameras"] = [
                updated.model_dump() if c.id == identity else c.model_dump()
                for c in runtime.settings.cameras
            ]
            validated = Settings.model_validate(data)
            if "password" in body or "username" in body:
                creds = runtime.secrets.get(identity).copy()
                for key in ("username", "password"):
                    if key in body:
                        if not isinstance(body[key], str) or len(body[key]) > 512:
                            raise ValueError("Camera sign-in details are too long.")
                        creds[key] = body[key]
                runtime.secrets.set(identity, **creds)
            runtime.settings = validated
            runtime.save()
        if set(body) & {"ip", "port", "path", "username", "password", "transcode_h264", "copy_h264"}:
            runtime.background(runtime.media.probe(updated))
        return {"ok": True}

    @app.get("/api/cameras/{identity}/presets")
    async def camera_presets(identity: str):
        camera = find_camera(identity)
        async with CameraControl(camera, runtime.secrets.get(identity)) as control:
            catalog = await control.list()
        if camera.control_protocol != "visca":
            records = {p["token"]: p["name"] for p in catalog["presets"]}
            async with runtime.write_lock:
                if camera is find_camera(identity) and camera.preset_records != records:
                    camera.preset_records = records
                    runtime.save()
        return catalog

    @app.post("/api/cameras/{identity}/presets/{action}")
    async def preset_action(identity: str, action: str, body: PresetAction):
        camera = find_camera(identity)
        if runtime.follow.handoffs.busy(identity) and action != "stop":
            raise HTTPException(409, "Pause tracking handoff before changing presets.")
        if identity in runtime.manual.leases and action != "stop":
            raise HTTPException(409, "Release manual control before using presets.")
        # Stop is allowed concurrently, so a pending recall cannot delay it.
        lock = runtime.preset_locks.setdefault(identity, asyncio.Lock())
        if action == "stop":
            async with CameraControl(camera, runtime.secrets.get(identity)) as control:
                return await control.execute(action)
        if lock.locked():
            raise HTTPException(409, "A preset command is already pending for this camera.")
        async with lock:
            async with CameraControl(camera, runtime.secrets.get(identity)) as control:
                runtime.follow.lab.command(identity, body.token, "sent")
                try:
                    result = await control.execute(action, body.token, body.name, body.speed)
                except BaseException:
                    runtime.follow.lab.command(identity, body.token, "error")
                    raise
                runtime.follow.lab.command(identity, body.token, "accepted")
                if camera.control_protocol == "visca" and action == "save":
                    runtime.save()
                if action == "recall":
                    runtime.settings.display.cameras = [identity]
                    runtime.settings.display.layout = "single"
                return result

    @app.delete("/api/cameras/{identity}")
    async def remove_camera(identity: str):
        camera = find_camera(identity)
        if runtime.follow.handoffs.busy(identity) or camera.handoff_mode == "live":
            raise ValueError("Pause tracking handoff before removing the camera.")
        runtime.follow.stop("Camera removed")
        async with runtime.write_lock:
            runtime.settings.cameras = [c for c in runtime.settings.cameras if c.id != identity]
            runtime.settings.display.cameras = [
                i for i in runtime.settings.display.cameras if i != identity
            ]
            runtime.save()
            runtime.secrets.remove(identity)
        await runtime.media.remove(identity)
        return {"ok": True}

    @app.post("/api/cameras/{identity}/retry")
    async def retry_camera(identity: str):
        runtime.background(runtime.media.probe(find_camera(identity)))
        return {"ok": True}

    @app.post("/api/cameras/{identity}/diagnose")
    async def diagnose_video(identity: str):
        camera = find_camera(identity)
        runtime.background(runtime.diagnostics.video(camera))
        return {"ok": True, "status": "Running"}

    @app.post("/api/cameras/{identity}/control-test")
    async def diagnose_control(identity: str):
        return await runtime.diagnostics.control(find_camera(identity))

    @app.get("/api/cameras/{identity}/diagnostics")
    async def export_camera_diagnostics(identity: str):
        find_camera(identity)
        return JSONResponse({"video": runtime.diagnostics.reports.get(identity), "control": runtime.diagnostics.controls.get(identity)}, headers={"Content-Disposition": f'attachment; filename="camera-{identity}-diagnostics.json"'})

    @app.post("/api/cameras/{identity}/speed-test")
    async def camera_speed_test(identity: str, body: dict):
        from confdeck.preset_speed import speed_test
        camera = find_camera(identity)
        if body.get("confirm") != "move between reserved presets" or runtime.follow.live or identity in runtime.manual.leases:
            raise ValueError("Stop Follow, release the joystick, and confirm movement between reserved presets.")
        if not camera.control_enabled or camera.control_protocol != "visca":
            raise ValueError("Enable VISCA control before running the speed test.")
        lock = runtime.preset_locks.setdefault(identity, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "Camera command already pending")
        async with lock:
            result = await speed_test(camera, str(body.get("origin", "")), str(body.get("destination", "")))
            runtime.save()
            return result

    @app.post("/api/cameras/{identity}/preset-speed")
    async def preset_speed_setting(identity: str, body: dict):
        camera = find_camera(identity)
        token, speed = str(body.get("token", "")), body.get("speed")
        if not camera.preset_speed_verified or token not in camera.preset_positions or type(speed) not in (float, int) or not .05 <= speed <= 1:
            raise ValueError("Verify speed support and save position readback before assigning preset speed.")
        camera.preset_speeds[token] = speed
        runtime.save()
        return {"ok": True}

    @app.post("/api/cameras/{identity}/test-move")
    async def test_move(identity: str, body: dict):
        camera = find_camera(identity)
        if body != {"confirm": "move right briefly using VISCA over IP"} or camera.demo:
            raise ValueError("Confirm the camera supports VISCA over IP before using a test move.")
        # Operator-requested 0.3 second low-speed right move, followed by STOP.
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=(camera.ip, 52381)
        )
        sequence = int(time.time()) & 0xFFFFFFFF

        def send(command, seq):
            packet = (
                b"\x01\x00" + len(command).to_bytes(2, "big") + seq.to_bytes(4, "big") + command
            )
            transport.sendto(packet)

        try:
            send(bytes.fromhex("8101060101010203ff"), sequence)
            await asyncio.sleep(0.3)
        finally:
            send(bytes.fromhex("8101060101010303ff"), (sequence + 1) & 0xFFFFFFFF)
            transport.close()
        return {"ok": True}

    @app.post("/api/metrics")
    async def metrics(body: Metrics):
        if body.camera:
            find_camera(body.camera)
            runtime.camera_metrics[body.camera] = {**body.model_dump(), "at": time.time()}
        return {"ok": True}

    @app.post("/api/mic")
    async def microphone(body: MicInput):
        return await save_microphone("primary", body)

    async def save_microphone(source, body, create=False):
        async with runtime.write_lock:
            data = runtime.settings.model_dump()
            previous = runtime.settings.mic if source == "primary" else next(
                (m for m in runtime.settings.microphones if m.id == source), None)
            if previous is None and not create:
                raise ValueError("Microphone not found.")
            value = body.settings.model_dump()
            if previous and (previous.ip, previous.port) != (body.settings.ip, body.settings.port):
                if value["tls_fingerprint"] == previous.tls_fingerprint:
                    value["tls_fingerprint"] = ""
                if body.password is None and body.settings.protocol == "sscv2":
                    raise ValueError("Enter the password when changing microphone address.")
            if source == "primary":
                data["mic"] = value
            else:
                value["id"] = source
                data["microphones"] = [m for m in data["microphones"] if m["id"] != source] + [value]
            candidate = Settings.model_validate(data)
            if body.password is not None:
                runtime.secrets.set("mic" if source == "primary" else f"mic:{source}",
                                    password=body.password.get_secret_value())
            # Keep unchanged adapter settings objects alive for certificate pin callbacks.
            if source == "primary":
                runtime.settings.mic = candidate.mic
                settings = runtime.settings.mic
            else:
                settings = next(m for m in candidate.microphones if m.id == source)
                runtime.settings.microphones = [m for m in runtime.settings.microphones if m.id != source] + [settings]
            runtime.save()
            await runtime.connect_mic(settings, source)
        return {"ok": True, "id": source}

    @app.post("/api/microphones")
    async def add_microphone(body: MicInput):
        return await save_microphone(AdditionalMic().id, body, create=True)

    @app.put("/api/microphones/{source}")
    async def edit_microphone(source: str, body: MicInput):
        return await save_microphone(source, body)

    @app.delete("/api/microphones/{source}")
    async def remove_microphone(source: str):
        async with runtime.write_lock:
            mic = next((m for m in runtime.settings.microphones if m.id == source), None)
            if mic is None:
                raise ValueError("Select an additional microphone.")
            if any(z.mic_id == source or source in z.microphone_profiles for z in runtime.settings.tracking.zones):
                raise ValueError("Remove this microphone's seats first, or disconnect it to keep the seat map.")
            mic.enabled = False
            await runtime.connect_mic(mic, source)
            runtime.settings.microphones = [m for m in runtime.settings.microphones if m.id != source]
            runtime.save()
            runtime.secrets.remove(f"mic:{source}")
        return {"ok": True}

    @app.get("/api/mic/raw")
    async def raw_mic():
        return list(runtime.mic.parser.raw) if runtime.mic else []

    @app.post("/api/mic/notice/dismiss")
    async def dismiss_mic_notice():
        runtime.store.dismiss_mic_notice()
        return {"ok": True}

    @app.post("/api/mic/test")
    async def test_microphone(source: str = "primary"):
        from confdeck.microphone import SSCv2Adapter

        mic = runtime.mic if source == "primary" else runtime.microphones.get(source)
        if not mic or not isinstance(mic, SSCv2Adapter):
            return JSONResponse(
                {"error": problem("invalid", "Connect a Ceiling Medium first.")},
                status_code=400,
            )
        try:
            stages = await mic.staged_test()
        except Exception as exc:
            log.warning("Staged test failed: %s", type(exc).__name__)
            stages = [
                {
                    "stage": 1,
                    "name": "Test",
                    "ok": False,
                    "detail": str(exc)[:200],
                    "fix": "Check the device.",
                }
            ]
        return {"stages": stages}

    @app.post("/api/mic/calibration")
    async def calibrate(body: dict):
        label = body.get("label", "")
        if not isinstance(label, str) or not 1 <= len(label) <= 80:
            raise ValueError("Enter a seat label, such as Under microphone or Seat 1.")
        if not runtime.mic or not runtime.mic.parser.position or runtime.mic.status()["age"] > 3:
            raise ValueError("Wait for a fresh microphone reading before recording this seat.")
        runtime.settings.calibration = (
            runtime.settings.calibration
            + [
                {
                    "label": label,
                    "reading": dict(runtime.mic.parser.values),
                    "settings": runtime.settings.mic.model_dump(),
                    "time": time.time(),
                }
            ]
        )[-100:]
        runtime.save()
        return {"ok": True}

    @app.websocket("/api/events/mic.position")
    async def mic_events(ws: WebSocket):
        if ws.headers.get("origin") != f"http://{ws.headers.get('host')}" or not identity(ws):
            await ws.close(code=4401)
            return
        if len(runtime.position_subscribers) >= 16:
            await ws.close(code=4429)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=16)
        runtime.position_subscribers.add(queue)
        try:
            while identity(ws):
                try:
                    event = await asyncio.wait_for(queue.get(), 5)
                except asyncio.TimeoutError:
                    event = {"type": "heartbeat"}
                await asyncio.wait_for(ws.send_json(event), 5)
        except (WebSocketDisconnect, RuntimeError, asyncio.TimeoutError):
            pass
        finally:
            runtime.position_subscribers.discard(queue)

    @app.post("/api/display")
    async def display_settings(body: DisplaySettings):
        known = {c.id for c in runtime.settings.cameras}
        if len(body.cameras) != len(set(body.cameras)) or not set(body.cameras) <= known:
            raise ValueError("Select each available camera once.")
        runtime.settings.display = body
        runtime.save()
        return {"ok": True}

    @app.post("/api/display/connect")
    async def connect_display():
        return {
            "code": runtime.auth.pairing(),
            "expires": runtime.auth.code_until,
            "url": f"http://{runtime.display_host}:{runtime.display_port}/display",
        }

    @app.post("/api/display/network")
    async def display_network(body: dict):
        from confdeck.discovery import networks

        host = body.get("host")
        if host not in {n["ip"] for n in networks()} | {"127.0.0.1"}:
            raise ValueError("Choose an address assigned to this laptop's local network.")
        if runtime.change_display_host is None:
            raise ValueError("Restart ConfDeck with --display-host followed by the chosen address.")
        await runtime.change_display_host(host)
        return {"ok": True}

    @app.get("/api/display/qr")
    async def display_qr():
        # Pairing code is intentionally separate from the URL / QR image.
        image = qrcode.make(f"http://{runtime.display_host}:{runtime.display_port}/display")
        data = io.BytesIO()
        image.save(data, format="PNG")
        return Response(data.getvalue(), media_type="image/png")

    @app.delete("/api/displays/{identity}")
    async def revoke_display(identity: str):
        runtime.store.revoke(identity)
        runtime.display_seen.pop(identity, None)
        return {"ok": True}

    @app.get("/api/monitors")
    async def monitors():
        return await asyncio.to_thread(runtime.monitors)

    @app.post("/api/monitors/{index}/open")
    async def open_monitor(index: int):
        await runtime.open_monitor(index)
        return {"ok": True}

    @app.get("/api/diagnostics")
    async def diagnostics():
        return runtime.diagnostics_snapshot()

    @app.post("/api/media/restart")
    async def restart_media():
        if runtime.media.process and runtime.media.process.returncode is None:
            runtime.media.process.terminate()
        return {"ok": True}

    @app.post("/api/demo/fault")
    async def fault(body: dict):
        if not runtime.demo:
            raise HTTPException(404, "Failure injection is only available in demo mode")
        kind = body.get("kind")
        if kind == "relay" and runtime.media.process:
            runtime.media.process.kill()
        elif kind == "mic-loss":
            runtime.mock.drop = bool(body.get("enabled", True))
        elif kind == "mic-garbage":
            runtime.mock.garbage = bool(body.get("enabled", True))
        elif kind == "camera-offline":
            raise ValueError("Camera simulation is unavailable. Connect a real camera.")
        return {"ok": True}

    return app
