"""Per-camera manual leases with an independent 500 ms deadman."""
import asyncio
import math
import time

from confdeck.ptz import CameraControl
from confdeck.visca import ViscaIpDriver


class CanonManualDriver:
    def __init__(self, camera, credentials):
        self.control = CameraControl(camera, credentials)

    async def drive(self, pan, tilt, zoom, speed):
        # Bounded relative steps also limit travel if the network drops mid-drag.
        params = {}
        for axis, value, span in (("pan", pan, 300), ("tilt", tilt, 300), ("zoom", -zoom, 200)):
            step = round(value * speed * span)
            params[f"c.1.{axis}"] = f"d{step}" if step else "stop"
        await self.control.canon("control.cgi", params)

    async def stop(self):
        try:
            await self.control.canon("control.cgi", {f"c.1.{axis}": "stop" for axis in ("pan", "tilt", "zoom")})
        finally:
            await self.control.client.aclose()


class ManualControl:
    def __init__(self, runtime):
        self.runtime = runtime
        self.leases = {}
        self.errors = {}

    async def release(self, camera_id, owner):
        lease = self.leases.get(camera_id)
        if not lease or lease["owner"] is not owner:
            return
        if lease.get("releasing"):
            return
        lease["releasing"] = True
        lease["task"].cancel()
        try:
            await asyncio.gather(lease["task"], return_exceptions=True)
            await lease["driver"].stop()
            self.runtime.follow.lab.command(camera_id, "manual-stop", "accepted")
        finally:
            if self.leases.get(camera_id) is lease:
                del self.leases[camera_id]

    async def disconnect(self, owner):
        await asyncio.gather(*(self.release(cid, owner) for cid in list(self.leases)), return_exceptions=True)

    async def accept(self, owner, data):
        if not isinstance(data, dict):
            raise ValueError("Invalid camera control message.")
        cid = data.get("camera")
        values = [float(data.get(k, 0)) for k in ("pan", "tilt", "zoom", "speed")]
        if not all(math.isfinite(v) and -1 <= v <= 1 for v in values):
            raise ValueError("Control values must be finite and between -1 and 1.")
        pan, tilt, zoom, speed = values
        if not any((pan, tilt, zoom)):
            await self.release(cid, owner)
            return
        camera = next((c for c in self.runtime.settings.cameras if c.id == cid), None)
        if not camera or not camera.control_enabled or camera.control_protocol not in ("visca", "canon") or camera.demo or camera.native_tracking:
            raise ValueError("Enable manual control with Canon XC or VISCA first.")
        lease = self.leases.get(cid)
        if lease and lease["owner"] is not owner:
            raise ValueError("Another controller is using this camera.")
        now = time.monotonic()
        if lease:
            if lease.get("releasing") or lease["task"].done():
                raise ValueError("Wait for the camera to stop.")
            if now - lease["updated"] < .09:
                return
            lease.update(updated=now, values=values)
            return
        if camera.handoff_mode != "off" or cid in self.runtime.follow.handoffs.armed:
            hand = self.runtime.follow.handoffs
            if cid in hand.manual_pending:
                return
            hand.manual_pending.add(cid)
            try:
                await hand.disarm(camera)
            finally:
                hand.manual_pending.discard(cid)
        lock = self.runtime.preset_locks.setdefault(cid, asyncio.Lock())
        if lock.locked():
            raise ValueError("Wait for the pending preset command to finish.")
        driver = (CanonManualDriver(camera, self.runtime.secrets.get(cid))
                  if camera.control_protocol == "canon" else ViscaIpDriver(camera))
        lease = dict(owner=owner, updated=now, values=values, driver=driver)
        self.leases[cid] = lease
        self.runtime.follow.lab.command(cid, "manual-drive", "sent")
        lease["task"] = asyncio.create_task(self.drive(cid, lease))
        asyncio.create_task(self.watch(cid, lease))

    async def watch(self, cid, lease):
        while self.leases.get(cid) is lease:
            await asyncio.sleep(.025)
            if time.monotonic() - lease["updated"] >= .5:
                try:
                    await self.release(cid, lease["owner"])
                except (ValueError, OSError) as exc:
                    self.runtime.follow.audit(f"STOP could not be confirmed: {exc}")
                return

    async def drive(self, cid, lease):
        try:
            while True:
                await lease["driver"].drive(*lease["values"])
                await asyncio.sleep(.1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors[lease["owner"]] = str(exc)
            self.runtime.follow.audit(f"Manual control error: {exc}")
            # Independent watchdog sends STOP; invalidate this lease immediately.
            lease["updated"] = 0
