"""Secure SSCv2 SSE, and deterministic mock microphone adapters."""

import asyncio
import contextlib
import hashlib
import json
import math
import random
import re
import ssl
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Protocol

import httpx

from confdeck.errors import problem
from confdeck.models import MicSettings
from confdeck.security import redact

_DEFAULT_RESOURCES = ["/api/audio/inputs/microphone/beam/direction"]
_KEEPALIVE_INTERVAL = 15  # seconds
_BACKOFF_MIN = 1
_BACKOFF_MAX = 30


class MicAdapter(Protocol):
    async def connect(self) -> None: ...
    async def subscribe(self) -> None: ...
    def status(self) -> dict: ...
    async def events(self): ...
    async def close(self) -> None: ...


class PositionParser:
    """Bounded incremental parser. Unknown/error fields never become beam readings."""

    def __init__(self, settings: MicSettings):
        self.settings = settings
        self.buffer = ""
        self.raw = deque(maxlen=200)
        self.arrivals = deque(maxlen=1000)
        self.history = deque(maxlen=120)
        self.values = {}
        self.times = {}
        self.last = 0.0
        self.bad = 0
        self.source_timestamp = float("-inf")
        self.position = None
        self.queue = asyncio.Queue(maxsize=16)
        self.level = None
        self.level_time = 0
        self.direction_revision = 0
        self.level_revision = 0
        # Partial notifications must not silently clear a reported far-end freeze.
        self.beam_freeze = False
        self.on_sample = None

    def feed(self, data: bytes) -> None:
        now = time.monotonic()
        text = data[:8192].decode("utf8", errors="replace")
        if self.buffer and text.lstrip().startswith('{"/api/'):
            self.buffer = ""
            self.bad += 1
        self.raw.append({"time": time.time(), "text": redact(text[:2048])})
        self.arrivals.append(now)
        self.buffer = (self.buffer + text)[-16384:]
        decoder = json.JSONDecoder()
        while self.buffer:
            self.buffer = self.buffer.lstrip("\r\n\x00 \t")
            if not self.buffer:
                break
            start = self.buffer.find("{")
            if start < 0:
                self.buffer = ""
                self.bad += 1
                break
            self.buffer = self.buffer[start:]
            try:
                obj, end = decoder.raw_decode(self.buffer)
            except (ValueError, RecursionError):
                # Discard a broken message before a new top-level message, but retain
                # a short fragment for JSON split across chunks.
                boundary = self.buffer.find("\n{")
                if boundary >= 0:
                    self.buffer = self.buffer[boundary + 1 :]
                    self.bad += 1
                    continue
                if len(self.buffer) > 8192 or self.buffer.endswith("}\n"):
                    self.buffer = ""
                    self.bad += 1
                break
            self.buffer = self.buffer[end:]
            if isinstance(obj, dict):
                self.consume(obj, now)

    def consume(self, obj: dict, now: float) -> None:
        if getattr(self, "on_raw", None):
            self.on_raw(obj, now)
        timestamp = obj.get("timestamp")
        if type(timestamp) in (int, float) and math.isfinite(timestamp):
            if timestamp <= self.source_timestamp:
                return
            self.source_timestamp = timestamp
        level_data = obj.get("/api/audio/inputs/microphone/level", {})
        level = level_data.get("peak") if isinstance(level_data, dict) else None
        if type(level) in (int, float) and math.isfinite(level) and -90 <= level <= 0:
            self.level, self.level_time = level, now
            self.level_revision += 1
        beam = {}
        # SSCv2 notifications are keyed by the full resource path.
        configured = getattr(self.settings, "resource_paths", None) or []
        for path, content in obj.items():
            if (
                path
                in (
                    "/api/m/beam",
                    "/api/audio/beam",
                    "/api/audio/inputs/microphone/beam/direction",
                )
                or path in configured and isinstance(content, dict) and any(
                    self.settings.field_map.get(k, k) in content for k in ("azimuth", "elevation"))
            ) and isinstance(content, dict):
                beam = content
            if path in ("/api/m/beam/azimuth", "/api/m/beam/elevation"):
                key = path.rsplit("/", 1)[-1]
                beam = {**beam, key: content.get(key) if isinstance(content, dict) else content}
        changed = False
        if not isinstance(beam, dict):
            return

        freeze_update = beam.get("beamFreezeActive")
        freeze_changed = False
        if type(freeze_update) is bool:
            freeze_changed = freeze_update != self.beam_freeze
            self.beam_freeze = freeze_update
            # A newer freeze-only notification must win over an in-flight poll.
            self.direction_revision += 1

        # Apply field_map: operator can choose which field names map to azimuth/elevation
        s = self.settings
        field_map = s.field_map if hasattr(s, "field_map") else {}
        az_field = field_map.get("azimuth", "azimuth")
        el_field = field_map.get("elevation", "elevation")

        for key, src_field, maximum in (
            ("azimuth", az_field, 360),
            ("elevation", el_field, 90),
        ):
            value = beam.get(src_field)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and 0 <= value <= maximum
            ):
                changed |= self.values.get(key) != value
                self.values[key] = value
                self.times[key] = now
                self.last = now
                self.direction_revision += 1

        # activity field (optional)
        act_field = field_map.get("activity")
        if act_field:
            activity = beam.get(act_field)
        else:
            activity = beam.get("activity")

        if not all(k in self.values and now - self.times[k] < 3 for k in ("azimuth", "elevation")):
            return
        azimuth = ((-1 if s.invert else 1) * self.values["azimuth"] + s.azimuth_offset) % 360
        elevation = self.values["elevation"]
        position = {
            "device_angles": [self.values["azimuth"], self.values["elevation"]],
            "azimuth": azimuth,
            "elevation": elevation,
            "activity": activity if isinstance(activity, bool) else None,
            "time": time.time(),
            "sample_time": now,
            "estimated_xy": None,
            "level": self.level if now - self.level_time < 1 else None,
            "beam_freeze": self.beam_freeze,
        }
        mapped_level = beam.get(field_map.get("level", "level"))
        if type(mapped_level) in (int, float) and math.isfinite(mapped_level):
            position["level"] = mapped_level
        if s.estimate_position or s.room_x or s.room_y or s.room_yaw:
            from confdeck.tracking_math.geometry import local_angles_to_room_point

            xy = local_angles_to_room_point(
                azimuth,
                elevation,
                ceiling_height=s.ceiling_height,
                head_height=s.head_height,
                room_x=getattr(s, "room_x", 0.0),
                room_y=getattr(s, "room_y", 0.0),
                room_yaw=getattr(s, "room_yaw", 0.0),
                azimuth_offset=0.0,
                invert=False,
                elevation_reference=s.elevation_reference,
            )
            if xy is not None:
                position["estimated_xy"] = [xy[0], xy[1]]
        self.position = position
        if changed:
            self.history.append(position)
        # Preserve change-only public events, while tracking gets fresh stationary samples.
        fresh = any(k in beam for k in (az_field, el_field))
        valid = all(type(beam[k]) in (int, float) and math.isfinite(beam[k]) and 0 <= beam[k] <= limit
                    for k, limit in ((az_field, 360), (el_field, 90)) if k in beam)
        if self.on_sample and (fresh or freeze_changed) and valid:
            self.on_sample(position)
        if changed:
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "mic.position", "data": position})

    def snapshot(self) -> dict:
        now = time.monotonic()
        age = now - self.last if self.last else None
        return {
            "position": self.position,
            "age": age,
            "rate": sum(now - t < 5 for t in self.arrivals) / 5,
            "malformed": self.bad,
            "history": list(self.history),
            "raw": list(self.raw),
            "stale_fields": [k for k in ("azimuth", "elevation") if now - self.times.get(k, 0) > 3],
        }


class BaseAdapter:
    def __init__(self, settings: MicSettings):
        self.settings = settings
        self.parser = PositionParser(settings)
        self.task = None
        self.state = "Connecting"
        self.issue = None
        self.identity = ""
        self.started = time.monotonic()

    async def connect(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def subscribe(self) -> None:
        pass

    def status(self) -> dict:
        data = self.parser.snapshot()
        state = self.state
        if data["age"] is not None and (data["age"] > 5 or data["stale_fields"]):
            state = "Reconnecting"
        issue = self.issue
        if data["age"] is None and time.monotonic() - self.started > 5 and state == "Connected":
            state, issue = "No position data", problem("protocol")
        return {
            **data,
            "state": state,
            "identity": self.identity,
            "issue": issue if state != "Connected" else None,
            "protocol": "mock" if isinstance(self, MockAdapter) else "sscv2",
        }

    async def events(self):
        while True:
            yield await self.parser.queue.get()

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class TCC2Adapter(BaseAdapter):
    """Read-only SSCv1 TCP metering for TeamConnect Ceiling 2.

    Polling also refreshes a stationary beam; no microphone settings are written.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.pending_beam = {}

    def status(self):
        return {**super().status(), "protocol": "tcc2"}

    def consume(self, obj):
        meter = obj.get("m", {}) if isinstance(obj, dict) else {}
        if not isinstance(meter, dict):
            return
        now = time.monotonic()
        self.parser.arrivals.append(now)
        beam = meter.get("beam", {})
        peak = meter.get("in1", {})
        payload = {}
        if isinstance(peak, dict) and "peak" in peak:
            payload["/api/audio/inputs/microphone/level"] = {"peak": peak["peak"]}
        if isinstance(beam, dict):
            self.pending_beam.update({k: beam[k] for k in ("azimuth", "elevation") if k in beam})
            if all(k in self.pending_beam for k in ("azimuth", "elevation")):
                payload["/api/audio/inputs/microphone/beam/direction"] = {
                    self.settings.field_map.get(k, k): self.pending_beam[k]
                    for k in ("azimuth", "elevation")}
                self.pending_beam.clear()
        if payload:
            self.parser.consume(payload, now)

    async def run(self):
        delay = _BACKOFF_MIN
        while True:
            writer = None
            poller = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.settings.ip, self.settings.port), 5)
                self.parser.values.clear()
                self.parser.times.clear()
                self.parser.level_time = 0
                self.pending_beam.clear()
                self.state, self.issue = "Connected", None
                delay = _BACKOFF_MIN

                async def poll():
                    queries = (b'{"m":{"in1":{"peak":null}}}\r\n'
                               b'{"m":{"beam":{"azimuth":null,"elevation":null}}}\r\n')
                    while True:
                        writer.write(queries)
                        await writer.drain()
                        await asyncio.sleep(.1)

                poller = asyncio.create_task(poll())
                buffer = ""
                decoder = json.JSONDecoder()
                while True:
                    data = await asyncio.wait_for(reader.read(8192), 4)
                    if not data:
                        raise ConnectionError("TCC 2 closed the connection")
                    buffer += data.decode("utf-8", errors="replace")
                    if len(buffer) > 32768:
                        raise ValueError("TCC 2 response too large")
                    while buffer.strip():
                        buffer = buffer.lstrip("\r\n \t\x00")
                        try:
                            obj, end = decoder.raw_decode(buffer)
                        except ValueError:
                            break
                        buffer = buffer[end:]
                        self.consume(obj)
            except (OSError, ValueError, TimeoutError, ConnectionError):
                self.state = "Reconnecting"
                self.issue = problem("protocol")
            finally:
                if poller:
                    poller.cancel()
                    await asyncio.gather(poller, return_exceptions=True)
                if writer:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
            await asyncio.sleep(delay)
            delay = min(_BACKOFF_MAX, delay * 2)


def _build_ssl_context_for_pinned(cert_der: bytes) -> ssl.SSLContext:
    """Build an SSLContext that trusts exactly one DER certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert_der))
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    # 0x200000 is OpenSSL X509_V_FLAG_NO_CHECK_TIME to allow self-signed device certificates
    # with fixed/expired manufacture dates while preserving strict fingerprint pinning.
    ctx.verify_flags |= getattr(ssl, "VERIFY_X509_NO_CHECK_TIME", 0x200000)
    return ctx


async def _fetch_cert_der(ip: str, port: int) -> bytes:
    """Open a bare TLS connection and return the peer certificate in DER form."""
    raw_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    raw_ctx.check_hostname = False
    raw_ctx.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, port, ssl=raw_ctx), 8
    )
    cert = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return cert


class SSCv2Adapter(BaseAdapter):
    """SSCv2 HTTPS/SSE adapter for the Sennheiser TeamConnect Ceiling Medium."""

    def __init__(
        self,
        settings: MicSettings,
        password: str,
        fingerprint_callback=None,
    ):
        super().__init__(settings)
        self.password = password
        # fingerprint_callback(fingerprint_hex, cert_der) is called when TOFU stores a new cert.
        self.fingerprint_callback = fingerprint_callback
        self._sub_uuid: str | None = None
        self.control_client = None

    async def resource_request(self, method, path, **kwargs):
        """Share the authenticated, certificate-pinned SSCv2 session."""
        if self.control_client is None or self.control_client.is_closed or self.state != "Connected":
            raise ValueError("Connect and test the Ceiling Medium microphone first.")
        response = await self.control_client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    async def run(self) -> None:
        delay = _BACKOFF_MIN
        while True:
            try:
                await self.session()
                delay = _BACKOFF_MIN
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 401:
                    self.issue = problem("mic_auth")
                elif code == 403:
                    self.issue = problem("mic_forbidden")
                elif code == 404:
                    self.issue = problem("mic_not_found")
                elif code == 409:
                    self.issue = problem("mic_conflict")
                elif code in (400, 422):
                    self.issue = problem("mic_bad_request")
                elif code >= 500:
                    self.issue = problem("mic_server")
                else:
                    self.issue = problem("protocol")
            except (httpx.ConnectError, httpx.ConnectTimeout, OSError):
                self.issue = problem("mic_unreachable")
            except ssl.SSLError:
                self.issue = problem("mic_tls")
            except ValueError as exc:
                msg = str(exc)
                if "fingerprint" in msg:
                    self.issue = problem("mic_fingerprint_changed")
                elif "subscription" in msg.lower():
                    self.issue = problem("mic_no_subscription")
                elif "json" in msg.lower() or "malformed" in msg.lower():
                    self.issue = problem("mic_malformed_json")
                else:
                    self.issue = problem("mic_no_identity") if "identity" in msg else problem("protocol")
            except (httpx.TransportError, httpx.TimeoutException):
                if not self.issue:
                    self.issue = problem("mic")
            self.state = "Reconnecting"
            jitter = random.uniform(0, delay * 0.3)
            await asyncio.sleep(delay + jitter)
            delay = min(_BACKOFF_MAX, delay * 2)

    async def session(self) -> None:
        self.issue = None
        port = self.settings.port
        base = f"https://{self.settings.ip}:{port}"

        # --- TLS: trust-on-first-use ---
        stored_fp = self.settings.tls_fingerprint
        if stored_fp:
            # Verify fingerprint before connecting
            cert_der = await _fetch_cert_der(self.settings.ip, port)
            actual_fp = hashlib.sha256(cert_der).hexdigest()
            if actual_fp != stored_fp:
                self.issue = problem("mic_fingerprint_changed")
                raise ValueError("fingerprint mismatch")
            ssl_ctx = _build_ssl_context_for_pinned(cert_der)
        else:
            # TOFU: fetch cert, compute fingerprint, store via callback
            cert_der = await _fetch_cert_der(self.settings.ip, port)
            fp = hashlib.sha256(cert_der).hexdigest()
            if self.fingerprint_callback:
                self.fingerprint_callback(fp, cert_der)
            # Build a context that trusts this cert for the rest of the session
            ssl_ctx = _build_ssl_context_for_pinned(cert_der)

        async with httpx.AsyncClient(
            base_url=base,
            auth=("api", self.password),
            verify=ssl_ctx,
            trust_env=False,
            timeout=httpx.Timeout(8, read=30),
            follow_redirects=False,
        ) as client:
            self.control_client = client
            # Identity check
            response = await client.get("/api/device/identity")
            response.raise_for_status()
            identity_data = response.json()
            product = ""
            if isinstance(identity_data, dict):
                product = (
                    identity_data.get("product")
                    or identity_data.get("name")
                    or identity_data.get("deviceName")
                    or ""
                )
            self.identity = json.dumps(identity_data)[:200]
            if not product:
                raise ValueError("identity missing product")
            # Warn if not a Ceiling Medium
            if "medium" not in str(product).lower() and "tccm" not in str(product).lower():
                self.identity = f"[unexpected model: {product}] " + self.identity

            # Open SSE subscription stream
            resources = getattr(self.settings, "resource_paths", None) or _DEFAULT_RESOURCES
            async with client.stream(
                "GET",
                "/api/ssc/state/subscriptions",
                headers={"Accept": "text/event-stream"},
            ) as stream_response:
                stream_response.raise_for_status()

                # Extract UUID from Content-Location header
                location = stream_response.headers.get("content-location", "")
                uuid_match = re.search(r"/api/ssc/state/subscriptions/([a-zA-Z0-9-]+)$", location)
                sub_uuid: str | None = uuid_match.group(1) if uuid_match else None
                self._sub_uuid = sub_uuid  # may be updated from open event

                # Subscribe URL (may be set after open event if header had no UUID)
                sub_url = f"/api/ssc/state/subscriptions/{sub_uuid}/add" if sub_uuid else None
                paths_added = False

                self.state = "Connected"

                # SSE line parser state
                event_type = ""
                data_lines: list[str] = []
                keepalive_task = asyncio.create_task(self._keepalive(client))
                try:
                    async for line in stream_response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line:
                            # Blank line = end of event
                            if data_lines:
                                raw_data = "\n".join(data_lines)
                                data_lines.clear()
                                new_uuid = await self._handle_sse_event(
                                    event_type, raw_data, client, resources, sub_uuid
                                )
                                if new_uuid and not sub_uuid:
                                    sub_uuid = new_uuid
                                    self._sub_uuid = sub_uuid
                                    sub_url = f"/api/ssc/state/subscriptions/{sub_uuid}/add"
                                # Add resource paths once we have the UUID
                                if sub_url and not paths_added:
                                    add_resp = await client.put(sub_url, json=resources)
                                    add_resp.raise_for_status()
                                    paths_added = True
                                    # Level is a FastResource in the official schema. Negotiate
                                    # separately so a device rejecting it cannot break direction SSE.
                                    level_path = "/api/audio/inputs/microphone/level"
                                    if level_path not in resources:
                                        try:
                                            level_response = await client.put(sub_url, json=[level_path])
                                            self.level_subscription = level_response.is_success
                                        except httpx.HTTPError:
                                            self.level_subscription = False
                                event_type = ""
                            if sum(map(len, data_lines)) > 16384:
                                data_lines.clear()
                        elif not line.startswith(":"):
                            # Unknown field, ignore
                            pass

                        # Check keepalive still alive
                        if keepalive_task.done() and keepalive_task.exception():
                            break
                finally:
                    keepalive_task.cancel()
                    await asyncio.gather(keepalive_task, return_exceptions=True)
                    # Clean up subscription
                    if sub_uuid:
                        with contextlib.suppress(Exception):
                            await client.delete(f"/api/ssc/state/subscriptions/{sub_uuid}")

    async def _handle_sse_event(
        self, event_type: str, raw_data: str, client, resources: list[str], sub_uuid: str | None
    ) -> str | None:
        """Handle one SSE event. Returns a subscription UUID if discovered from the open event."""
        if not raw_data:
            return None
        try:
            obj = json.loads(raw_data)
        except (ValueError, RecursionError):
            self.parser.bad += 1
            return None

        if event_type == "open" or (event_type == "" and isinstance(obj, dict) and "sessionUUID" in obj):
            # Extract UUID from open event if not already from header
            discovered_uuid = None
            if not self._sub_uuid:
                uuid_val = obj.get("sessionUUID") or obj.get("uuid")
                if uuid_val and re.fullmatch(r"[a-zA-Z0-9-]{1,80}", str(uuid_val)):
                    self._sub_uuid = str(uuid_val)
                    discovered_uuid = self._sub_uuid
            return discovered_uuid

        elif event_type == "close":
            # Device closed the stream — will trigger reconnect via stream end
            self.issue = problem("mic_stream_closed")

        elif event_type in ("message", ""):
            # Position data
            if isinstance(obj, dict):
                self.parser.feed((raw_data + "\n").encode())

        return None

    async def _keepalive(self, client: httpx.AsyncClient) -> None:
        """Periodic lightweight GET to detect dead connections."""
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            try:
                resp = await client.get("/api/ssc/version", timeout=8)
                resp.raise_for_status()
            except Exception:
                # If keepalive fails, the main stream loop will notice on next event
                break

    async def staged_test(self) -> list[dict]:
        """
        Run a 6-stage connection test. Returns a list of stage dicts:
        {"stage": int, "name": str, "ok": bool, "detail": str, "fix": str}
        Stops at the first failure.
        """
        stages = []
        port = self.settings.port
        ip = self.settings.ip

        def pass_stage(n, name, detail=""):
            stages.append({"stage": n, "name": name, "ok": True, "detail": detail, "fix": ""})

        def fail_stage(n, name, detail, fix):
            stages.append({"stage": n, "name": name, "ok": False, "detail": detail, "fix": fix})
            return stages

        # Stage 1: reachable
        cert_der = None
        try:
            cert_der = await _fetch_cert_der(ip, port)
            pass_stage(1, "Reachable", f"{ip}:{port} responded")
        except (OSError, asyncio.TimeoutError) as exc:
            return fail_stage(
                1, "Reachable",
                f"Could not connect to {ip}:{port}: {exc}",
                "Check the control IP (not the Dante IP), power, and subnet."
            )

        # Stage 2: TLS fingerprint
        fp = hashlib.sha256(cert_der).hexdigest()
        fp_display = ":".join(fp[i:i+2] for i in range(0, len(fp), 2))
        stored_fp = self.settings.tls_fingerprint
        if not stored_fp:
            self.settings.tls_fingerprint = fp
            if self.fingerprint_callback:
                self.fingerprint_callback(fp, cert_der)
            pass_stage(2, "TLS handshake", f"New certificate trusted. SHA-256: {fp_display}")
        else:
            if fp != stored_fp:
                return fail_stage(
                    2, "TLS fingerprint",
                    f"Fingerprint changed! Got {fp_display}",
                    "If the device was replaced or reset, clear the stored fingerprint and re-run."
                )
            pass_stage(2, "TLS fingerprint", "Fingerprint matches stored value.")
        ssl_ctx = _build_ssl_context_for_pinned(cert_der)

        async with httpx.AsyncClient(
            base_url=f"https://{ip}:{port}",
            auth=("api", self.password),
            verify=ssl_ctx,
            trust_env=False,
            timeout=httpx.Timeout(8, read=15),
            follow_redirects=False,
        ) as client:
            # Stage 3: login (use /api/ssc/version as a lightweight auth check)
            try:
                r = await client.get("/api/ssc/version")
                if r.status_code == 401:
                    return fail_stage(
                        3, "Login",
                        "401 Unauthorized",
                        "Username is 'api'. Enable third-party API access in Control Cockpit › Access."
                    )
                if r.status_code == 403:
                    return fail_stage(
                        3, "Login",
                        "403 Forbidden",
                        "Enable third-party API access in Sennheiser Control Cockpit › Access tab."
                    )
                r.raise_for_status()
                pass_stage(3, "Login", f"Authenticated (version: {r.text[:80]})")
            except httpx.HTTPStatusError as exc:
                return fail_stage(
                    3, "Login",
                    f"HTTP {exc.response.status_code}",
                    "Check credentials and third-party access."
                )
            except Exception as exc:
                return fail_stage(3, "Login", str(exc)[:200], "Check credentials.")

            # Stage 4: device identity
            try:
                r = await client.get("/api/device/identity")
                r.raise_for_status()
                data = r.json()
                product = ""
                if isinstance(data, dict):
                    product = (
                        data.get("product") or data.get("name") or
                        data.get("deviceName") or ""
                    )
                if not product:
                    return fail_stage(
                        4, "Device identity",
                        "No product name in response",
                        "Confirm firmware and third-party access."
                    )
                warn = ""
                if "medium" not in str(product).lower() and "tccm" not in str(product).lower():
                    warn = f" ⚠ Unexpected model: {product}"
                pass_stage(4, "Device identity", f"Product: {product}{warn}")
            except Exception as exc:
                return fail_stage(4, "Device identity", str(exc)[:200],
                                  "Confirm firmware and third-party access.")

            # Stage 5: open subscription
            resources = getattr(self.settings, "resource_paths", None) or _DEFAULT_RESOURCES
            sub_uuid = None
            first_data = None
            try:
                async with client.stream(
                    "GET", "/api/ssc/state/subscriptions",
                    headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(8, read=10),
                ) as resp:
                    resp.raise_for_status()
                    location = resp.headers.get("content-location", "")
                    m = re.search(r"/api/ssc/state/subscriptions/([a-zA-Z0-9-]+)$", location)
                    if m:
                        sub_uuid = m.group(1)

                    # Read first few SSE events to find open event and get UUID
                    event_type = ""
                    data_lines: list[str] = []
                    paths_subscribed = False
                    async for line in resp.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            raw = "\n".join(data_lines)
                            data_lines.clear()
                            try:
                                obj = json.loads(raw)
                                if event_type == "open" or (isinstance(obj, dict) and "sessionUUID" in obj):
                                    if not sub_uuid:
                                        sub_uuid = str(obj.get("sessionUUID", ""))
                                    if sub_uuid and not paths_subscribed:
                                        add_r = await client.put(
                                            f"/api/ssc/state/subscriptions/{sub_uuid}/add",
                                            json=resources,
                                        )
                                        if add_r.status_code == 404:
                                            return fail_stage(
                                                5, "Subscription",
                                                f"PUT /add returned 404 for paths: {resources}",
                                                "Edit resource paths in Microphone settings."
                                            )
                                        add_r.raise_for_status()
                                        paths_subscribed = True
                                elif event_type in ("message", "") and first_data is None:
                                    first_data = obj
                            except (ValueError, RecursionError):
                                pass
                            event_type = ""
                            if sub_uuid and first_data is not None:
                                break

                if not sub_uuid:
                    return fail_stage(
                        5, "Subscription",
                        "No subscription UUID received",
                        "Check firmware; third-party access must be enabled."
                    )

                if not paths_subscribed:
                    add_r = await client.put(
                        f"/api/ssc/state/subscriptions/{sub_uuid}/add", json=resources
                    )
                    if add_r.status_code == 404:
                        return fail_stage(
                            5, "Subscription",
                            f"Resource path not found: {resources}",
                            "Edit the resource path list in Microphone settings."
                        )
                    add_r.raise_for_status()

                pass_stage(5, "Subscription", f"UUID: {sub_uuid}")

            except httpx.HTTPStatusError as exc:
                return fail_stage(5, "Subscription", f"HTTP {exc.response.status_code}",
                                  "Check firmware and resource paths.")
            except Exception as exc:
                return fail_stage(5, "Subscription", str(exc)[:200], "Check device.")
            finally:
                if sub_uuid:
                    with contextlib.suppress(Exception):
                        await client.delete(f"/api/ssc/state/subscriptions/{sub_uuid}")

            # Stage 6: first data
            if first_data is not None:
                pass_stage(6, "First data", f"Received: {str(first_data)[:120]}")
            else:
                pass_stage(
                    6, "First data",
                    "No data received yet (normal — data arrives only on change).",
                    ""
                )

        return stages


class MockAdapter(BaseAdapter):
    scenario = "jitter"
    drop = False
    garbage = False

    async def run(self) -> None:
        self.identity = "Simulated ceiling microphone"
        while True:
            for row in tracking_scenario(self.scenario):
                if not self.drop:
                    if self.garbage:
                        self.parser.feed(b'{"error": broken}\n')
                    self.parser.feed(json.dumps({_DEFAULT_RESOURCES[0]: row["event"]}).encode())
                    self.state = "Connected"
                await asyncio.sleep(.1)


def tracking_scenario(name="jitter", centers=None):
    """Deterministic raw mic recordings for room-free tuning and regression tests."""
    centers = centers or [(45, 45), (220, 45)]
    rows = []
    for i in range(400):
        t = i / 10
        center = centers[0 if t < 10 else 1]
        if name == "flapping":
            center = centers[i % 2]
        a = (center[0] + 2 * math.sin(i * 1.7)) % 360
        talking = t < 22
        if name == "noise":
            talking = i % 30 < 2
        rows.append({"t": t, "event": {"azimuth": a, "elevation": center[1] + math.sin(i),
                    "activity": talking, "level": -35 if talking else -85}})
    return rows


# ---------------------------------------------------------------------------
# Mock SSCv2 HTTPS server for tests (self-signed cert, SSE, /add, DELETE)
# ---------------------------------------------------------------------------

def _make_self_signed_cert():
    """Generate a minimal self-signed RSA cert+key pair as (cert_pem, key_pem, cert_der)."""
    try:
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "tccm-test"),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        cert_der = cert.public_bytes(serialization.Encoding.DER)
        return cert_pem, key_pem, cert_der
    except ImportError:
        return None, None, None


class MockSSCv2Server:
    """
    Local HTTPS/SSE server simulating a TCC M for tests and demo mode.
    Supports error injection: auth_fail, not_found, drop_connection,
    close_event, slow_start, malformed_json.
    """

    def __init__(self, password: str = "test-password"):
        self.password = password
        self.error_mode: str | None = None  # "auth_fail"|"not_found"|"drop"|"close"|"malformed"
        self.slow_start = False
        self._server = None
        self._cert_pem = None
        self._key_pem = None
        self._cert_der = None
        self._tmpdir = None
        self._port = None
        self._subs: dict[str, asyncio.Queue] = {}
        self._emit_task = None
        self.settings_values = {}

    @property
    def fingerprint(self) -> str | None:
        if self._cert_der:
            return hashlib.sha256(self._cert_der).hexdigest()
        return None

    @property
    def port(self) -> int | None:
        return self._port

    async def start(self) -> int:
        cert_pem, key_pem, cert_der = _make_self_signed_cert()
        if cert_pem is None:
            raise RuntimeError("cryptography package not available for MockSSCv2Server")
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._cert_der = cert_der

        import os
        self._tmpdir = tempfile.mkdtemp()
        cert_path = os.path.join(self._tmpdir, "cert.pem")
        key_path = os.path.join(self._tmpdir, "key.pem")
        Path(cert_path).write_bytes(cert_pem)
        Path(key_path).write_bytes(key_pem)

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert_path, key_path)

        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, ssl=ssl_ctx
        )
        self._port = self._server.sockets[0].getsockname()[1]
        self._emit_task = asyncio.create_task(self._emitter())
        return self._port

    async def stop(self):
        if self._emit_task:
            self._emit_task.cancel()
            await asyncio.gather(self._emit_task, return_exceptions=True)
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._tmpdir:
            import shutil
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    async def _emitter(self):
        """Push moving beam/direction values to all open SSE queues."""
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            payload = json.dumps({
                "/api/audio/inputs/microphone/beam/direction": {
                    "azimuth": round((now * 13) % 360, 1),
                    "elevation": round(30 + 12 * math.sin(now / 3), 1),
                }
            })
            for q in list(self._subs.values()):
                if not q.full():
                    q.put_nowait(payload)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                line_bytes = await reader.readline()
                if not line_bytes:
                    break
                request_line = line_bytes.decode("latin1").strip()
                if not request_line:
                    continue
                headers = {}
                while True:
                    line = (await reader.readline()).decode("latin1").strip()
                    if not line:
                        break
                    if ":" in line:
                        k, _, v = line.partition(":")
                        headers[k.strip().lower()] = v.strip()

                parts = request_line.split(" ", 2)
                if len(parts) < 2:
                    break
                method, path = parts[0], parts[1]

                # Check auth
                import base64

                auth_header = headers.get("authorization", "")
                expected = "Basic " + base64.b64encode(f"api:{self.password}".encode()).decode()
                if self.error_mode == "auth_fail" or auth_header != expected:
                    writer.write(
                        b"HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic\r\nContent-Length: 0\r\n\r\n"
                    )
                    await writer.drain()
                    break

                if self.slow_start:
                    await asyncio.sleep(2)

                # Route
                if path in self.settings_values:
                    if method == "PUT":
                        length = int(headers.get("content-length", 0))
                        value = json.loads(await reader.readexactly(length))
                        self.settings_values[path].update(value)
                    body = json.dumps(self.settings_values[path])
                    writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode())
                    await writer.drain()

                elif path == "/api/device/identity":
                    body = json.dumps({"product": "TeamConnect Ceiling Medium", "version": "1.0.0"})
                    writer.write(
                        f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode()
                    )
                    await writer.drain()

                elif path == "/api/ssc/version":
                    body = '{"version":"1.0.0"}'
                    writer.write(
                        f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode()
                    )
                    await writer.drain()

                elif method == "GET" and path == "/api/ssc/state/subscriptions":
                    if self.error_mode == "not_found":
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        break

                    sub_uuid = f"test-{int(time.monotonic() * 1000) % 100000}"
                    q: asyncio.Queue = asyncio.Queue(maxsize=50)
                    initial_payload = json.dumps({
                        "/api/audio/inputs/microphone/beam/direction": {
                            "azimuth": 90.0,
                            "elevation": 45.0,
                        }
                    })
                    q.put_nowait(initial_payload)
                    self._subs[sub_uuid] = q
                    session_path = f"/api/ssc/state/subscriptions/{sub_uuid}"

                    if self.error_mode == "drop":
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n")
                        writer.write(f"Content-Location: {session_path}\r\n\r\n".encode())
                        await writer.drain()
                        self._subs.pop(sub_uuid, None)
                        break

                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n")
                    writer.write(f"Content-Location: {session_path}\r\n\r\n".encode())
                    # Send open event
                    open_payload = json.dumps({"sessionUUID": sub_uuid})
                    writer.write(f"event: open\ndata: {open_payload}\n\n".encode())
                    await writer.drain()

                    if self.error_mode == "malformed":
                        writer.write(b"event: message\ndata: {broken json\n\n")
                        await writer.drain()

                    if self.error_mode == "close":
                        writer.write(b"event: close\ndata: {}\n\n")
                        await writer.drain()
                        self._subs.pop(sub_uuid, None)
                        break

                    # Stream data events from the emitter queue
                    try:
                        while True:
                            try:
                                payload = await asyncio.wait_for(q.get(), timeout=1.0)
                                writer.write(f"event: message\ndata: {payload}\n\n".encode())
                                await writer.drain()
                            except asyncio.TimeoutError:
                                # Send a comment keepalive
                                writer.write(b": keepalive\n\n")
                                await writer.drain()
                    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                        pass
                    finally:
                        self._subs.pop(sub_uuid, None)
                    break

                elif method == "PUT" and "/add" in path:
                    # Subscription add endpoint
                    content_length = int(headers.get("content-length", 0))
                    if content_length > 0:
                        await reader.read(content_length)
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                    await writer.drain()

                elif method == "DELETE" and "/api/ssc/state/subscriptions/" in path:
                    # Subscription delete
                    sub_id = path.rsplit("/", 1)[-1]
                    self._subs.pop(sub_id, None)
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                    await writer.drain()

                else:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()

        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
