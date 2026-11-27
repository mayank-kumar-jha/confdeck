"""VISCA control, position readback and capability-qualified preset speed."""

import asyncio
from contextlib import contextmanager
import secrets
import socket


class ViscaIpDriver:
    def __init__(self, camera, persistent=False):
        self.camera = camera
        self.persistent = persistent and camera.visca_port == 52381 and not camera.visca_local_port
        self.socket = None
        self.completion = None
        self.local_port = camera.visca_local_port

    @property
    def canon(self):
        return self.camera.control_protocol == "canon" or "canon" in (self.camera.model+self.camera.name).lower()

    def preset_number(self, token):
        lower, upper = (1, 100) if self.canon else (0, 127)
        if not str(token).isascii() or not str(token).isdigit() or not lower <= int(token) <= upper:
            raise ValueError(f"Preset numbers must be {lower}–{upper} for this camera.")
        return int(token)-(1 if self.canon else 0)

    async def recall(self, token):
        if not self.camera.control_enabled or self.camera.demo:
            raise ValueError("Enable camera control before recalling a preset.")
        number = self.preset_number(token)
        position = self.camera.preset_positions.get(str(token))
        start = asyncio.get_running_loop().time()
        approach = False
        if self.camera.preset_speed_verified and position and not self.canon:
            try:
                await self.absolute(position, self.camera.preset_speeds.get(str(token), .25))
                await self.settled(position)
                approach = True
            except (ValueError, OSError, TimeoutError):
                self.camera.preset_speed_verified = False
        payload = bytes([0x81, 0x01, 0x04, 0x3F, 0x02, number, 0xFF])
        reply = await self._exchange(payload)
        reply_ms = (asyncio.get_running_loop().time()-start)*1000
        settle_ms = reply_ms if reply and reply[1] & 0xf0 == 0x50 else None
        if approach:
            try:
                await self.settled(position)
                settle_ms = (asyncio.get_running_loop().time()-start)*1000
            except (ValueError, OSError, TimeoutError):
                self.camera.preset_speed_verified = False
                await self._exchange(payload)
        return {"ok": True, "status": "VISCA command acknowledged", "reply_ms": reply_ms, "settle_ms": settle_ms, "speed_honored": approach and self.camera.preset_speed_verified}

    async def save(self, token):
        if not self.camera.control_enabled or self.camera.demo:
            raise ValueError("Enable control on a real camera before saving.")
        if str(token).isdigit() and int(token) in self.camera.protected_presets:
            raise ValueError("Protected tracking preset cannot be overwritten.")
        if (not str(token).isascii() or not str(token).isdigit()
                or not self.camera.preset_start <= int(token) <= self.camera.preset_end):
            raise ValueError("Choose a slot inside the reserved preset range.")
        await self._exchange(bytes([0x81, 1, 4, 0x3F, 1, self.preset_number(token), 0xFF]))
        self.camera.preset_positions.pop(str(token), None)
        if self.camera.preset_speed_verified and not self.canon:
            try:
                self.camera.preset_positions[str(token)] = await self.position()
            except (ValueError, OSError, TimeoutError):
                self.camera.preset_speed_verified = False
        return {"ok": True, "status": "Save acknowledged; recall to verify framing"}

    async def drive(self, pan, tilt, zoom, speed):
        scale = max(.05, abs(speed))
        ps = max(1, min(24, round(abs(pan) * scale * 24)))
        ts = max(1, min(20, round(abs(tilt) * scale * 20)))
        pd = 3 if abs(pan) < .05 else (2 if pan > 0 else 1)
        td = 3 if abs(tilt) < .05 else (1 if tilt > 0 else 2)
        z = 0 if abs(zoom) < .05 else ((0x20 if zoom > 0 else 0x30) | min(7, round(abs(zoom) * scale * 7)))
        await self._exchange(bytes([0x81, 1, 6, 1, ps, ts, pd, td, 0xFF]))
        if z != 0 or getattr(self, "_zooming", False):
            await self._exchange(bytes([0x81, 1, 4, 7, z, 0xFF]))
            self._zooming = (z != 0)

    async def check(self):
        """Verify control without moving the camera or changing its power state."""
        for port in ([0, 52381] if self.canon else [self.local_port]):
            self.local_port = port
            for payload in ("81 09 00 02 FF", "81 09 06 12 FF", "81 09 04 47 FF"):
                try:
                    await self._exchange(bytes.fromhex(payload), inquiry=True)
                    self.camera.visca_local_port = port
                    return {"ok": True, "probe": "Version/position inquiry replied"}
                except (ValueError, OSError, TimeoutError):
                    continue
        raise ValueError("Control not responding. Enable IP control; allow inbound UDP 52381 for Canon Standard Communication.")

    async def position(self):
        pt = await self._exchange(bytes.fromhex("81 09 06 12 FF"), inquiry=True)
        zoom = await self._exchange(bytes.fromhex("81 09 04 47 FF"), inquiry=True)
        def value(data):
            if any(n > 15 for n in data):
                raise ValueError("Invalid VISCA position nibble")
            return sum(n << (4*(3-i)) for i, n in enumerate(data))
        return value(pt[2:6]), value(pt[6:10]), value(zoom[2:6])

    async def absolute(self, position, speed):
        if not self.camera.control_enabled or self.camera.demo or self.canon:
            raise ValueError("Absolute speed control is unavailable for this camera")
        def nibbles(value):
            return bytes((value >> n) & 15 for n in (12, 8, 4, 0))
        pan, tilt, zoom = position
        speed = max(.05, min(1., speed))
        await self._exchange(bytes([0x81, 1, 6, 2, max(1, round(speed*24)), max(1, round(speed*20))])+nibbles(pan)+nibbles(tilt)+b"\xff")
        await self._exchange(b"\x81\x01\x04\x47"+nibbles(zoom)+b"\xff")

    async def settled(self, target=None, timeout=15):
        loop = asyncio.get_running_loop()
        start, stable, previous = loop.time(), None, None
        current = None
        while loop.time()-start < timeout:
            current = await self.position()
            matches = target is None or all(abs(a-b) <= 2 for a, b in zip(current, target))
            if current == previous and matches:
                stable = stable if stable is not None else loop.time()
                if loop.time()-stable >= .15:
                    return {"position": current, "ms": (loop.time()-start)*1000}
            else:
                stable = None
            previous = current
            await asyncio.sleep(.05)
        raise ValueError(f"Position did not settle within {timeout}s: expected {target}, last readback {current}; tolerance 2 position units, stable 150 ms")

    async def stop(self):
        if self.camera.demo:
            raise ValueError("Use a real camera for VISCA control.")
        results = await asyncio.gather(
            self._exchange(bytes.fromhex("81 01 06 01 01 01 03 03 FF")),
            self._exchange(bytes.fromhex("81 01 04 07 00 FF")), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                raise result
        return {"ok": True, "status": "VISCA stop acknowledged"}

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    @contextmanager
    def connection(self):
        sock = self.socket
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if self.socket is None:
                sock.setblocking(False)
                if self.local_port:
                    sock.bind(("", self.local_port))
                sock.connect((self.camera.ip, 52381 if self.canon else self.camera.visca_port))
                if self.persistent:
                    self.socket = sock
            yield sock
        except BaseException:
            self.close()
            raise
        finally:
            if not self.persistent or self.socket is None:
                sock.close()

    async def wait_for_completion(self):
        """Listen for the matching framed VISCA completion on the retained socket."""
        if not self.socket or not self.completion:
            return False
        seq, command_socket = self.completion
        while True:
            reply = await asyncio.get_running_loop().sock_recv(self.socket, 1024)
            if len(reply) == 11 and reply[:4] == bytes.fromhex("01 11 00 03") and reply[4:8] == seq:
                if reply[8:] == bytes([0x90, 0x50 | command_socket, 0xff]):
                    self.completion = None
                    return True
                if reply[9] & 0xf0 == 0x60:
                    raise ValueError("Camera reported an error before completing the command.")

    async def _exchange(self, payload, inquiry=False):
        self.completion = None
        seq = secrets.randbits(32).to_bytes(4, "big")
        kind = b"\x01\x10" if inquiry else b"\x01\x00"
        packet = kind + len(payload).to_bytes(2, "big") + seq + payload
        is_sony_port = (self.camera.visca_port == 52381)
        first_packet = packet if is_sony_port else payload
        second_packet = payload if is_sony_port else packet
        loop = asyncio.get_running_loop()
        with self.connection() as sock:
            await loop.sock_sendall(sock, first_packet)
            deadline = loop.time() + 3
            fallback_sent = False
            while loop.time() < deadline:
                try:
                    wait_time = min(0.25 if not fallback_sent else 2.5, max(0.05, deadline - loop.time()))
                    reply = await asyncio.wait_for(loop.sock_recv(sock, 1024), wait_time)
                except (TimeoutError, OSError):
                    if not fallback_sent and not self.canon:
                        fallback_sent = True
                        await loop.sock_sendall(sock, second_packet)
                        continue
                    raise ValueError("No VISCA acknowledgement. Check control protocol, port and camera power.") from None
                if len(reply) >= 11 and reply[:2] == b"\x01\x11" and reply[4:8] == seq:
                    if int.from_bytes(reply[2:4], "big") != len(reply) - 8:
                        continue
                    data = reply[8:]
                elif len(reply) >= 3 and reply[0] == 0x90 and reply[-1] == 0xFF:
                    # Raw replies have no sequence number; isolate subsequent commands.
                    self.persistent = False
                    self.socket = None
                    data = reply
                else:
                    continue
                if data[0] != 0x90 or data[-1] != 0xFF:
                    continue
                if data[1] & 0xF0 == 0x60:
                    raise ValueError(f"Camera rejected VISCA command {payload.hex(' ')}; response {data.hex(' ')}")
                if inquiry:
                    expected = {bytes.fromhex("81 09 00 02 FF"): 10, bytes.fromhex("81 09 06 12 FF"): 11, bytes.fromhex("81 09 04 47 FF"): 7}.get(payload)
                    if expected and len(data) == expected and data[1] == 0x50:
                        return data
                elif len(data) == 3 and data[1] & 0xF0 in (0x40, 0x50):
                    if self.socket and data[1] & 0xf0 == 0x40:
                        self.completion = (seq, data[1] & 15)
                    return data
            raise ValueError("No matching VISCA acknowledgement received.")
