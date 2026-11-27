"""Optional, operator-verified camera tracking downstream of seat decisions."""
import asyncio
import time
from collections import defaultdict, deque
from statistics import median
from typing import Literal

import httpx
from pydantic import BaseModel, Field, model_validator

from confdeck.visca import ViscaIpDriver


class Control(BaseModel):
    kind: Literal["none", "visca_custom", "visca_preset", "http"] = "none"
    on: str = Field(default="", max_length=4096)
    off: str = Field(default="", max_length=4096)
    method: Literal["GET", "POST", "PUT"] = "POST"
    path: str = Field(default="/", max_length=512)
    headers: dict[str, str] = Field(default_factory=dict, max_length=16)
    settle_ms: int = Field(default=1000, ge=100, le=15000)

    @model_validator(mode="after")
    def valid(self):
        if self.kind == "none":
            return self
        if self.on == self.off:
            raise ValueError("ON and OFF must differ.")
        for value in (self.on, self.off):
            if self.kind == "visca_custom":
                payload = bytes.fromhex(value)
                if not 1 <= len(payload) <= 32 or 255 in payload or payload[0] >= 128:
                    raise ValueError("Enter command bytes without the address or FF terminator.")
            if self.kind == "visca_preset" and (not value.isdecimal() or not 0 <= int(value) <= 127):
                raise ValueError("Enter a preset from 0 to 127.")
        if self.kind == "http" and (not self.path.startswith("/") or self.path.startswith("//") or "\\" in self.path):
            raise ValueError("Enter a path on this camera.")
        return self


class Handoffs:
    def __init__(self, runtime):
        self.runtime = runtime
        self.locks = defaultdict(asyncio.Lock)
        self.states = {}
        self.armed = set()
        self.samples = defaultdict(lambda: deque(maxlen=100))
        self.trials = {}
        self.drivers = {}
        self.connected = {}
        self.active = {}
        self.manual_pending = set()

    def tick(self, follow, now):
        if not follow.live or follow.pending_decision or (follow.task and not follow.task.done()):
            return
        for camera in self.runtime.settings.cameras:
            cid = camera.id
            active = self.active.get(cid)
            if not active or cid not in self.armed or camera.handoff_mode != "live" or cid in self.runtime.manual.leases:
                continue
            seat, token, since = active
            voice = follow.engine.last_voice
            idle = voice is not None and camera.handoff_silence_s and now-voice >= camera.handoff_silence_s
            reanchor = (camera.handoff_reanchor_s and now-since >= camera.handoff_reanchor_s
                        and follow.engine.current == seat and voice is not None and now-voice < 1
                        and not follow.engine.candidate)
            if not idle and not reanchor:
                continue
            if idle:
                wide = self.runtime.settings.tracking.wide_presets.get(cid)
                if not wide:
                    follow.task = self.runtime.background(self.disarm(camera))
                    return
                seat, token = "wide", wide
            follow.camera_frames.pop(cid, None)
            follow.dispatch({"zone":seat, "presets":{cid:token}, "time":now})
            return

    def driver(self, camera):
        driver = self.drivers.get(camera.id)
        if not driver or driver.camera is not camera:
            if driver:
                driver.close()
            driver = self.drivers[camera.id] = ViscaIpDriver(camera, persistent=True)
        return driver

    async def reconnect(self, camera, online):
        old = self.connected.get(camera.id)
        self.connected[camera.id] = online
        if old is False and online and camera.handoff_mode == "live":
            self.runtime.follow.stop("Camera reconnected; resetting handoff")
            await self.disarm(camera, force=True)

    def busy(self, cid):
        return cid in self.armed or self.locks[cid].locked()

    def record(self, camera):
        return self.runtime.secrets.get("handoff-" + camera.id)

    def config(self, camera):
        record = self.record(camera)
        if record.get("ip") != camera.ip or record.get("ports", [camera.control_port,camera.visca_port]) != [camera.control_port,camera.visca_port]:
            raise ValueError("Verify the tracking controls on this camera address.")
        return Control.model_validate_json(record["config"])

    def transition(self, cid, state, trace):
        trace[state] = time.monotonic()
        self.states[cid] = {"state": state, "since": time.time(), "trace": trace.copy()}

    async def send(self, camera, cfg, enabled):
        value = cfg.on if enabled else cfg.off
        driver = self.driver(camera)
        if cfg.kind == "visca_custom":
            await driver._exchange(bytes([0x81]) + bytes.fromhex(value) + bytes([0xff]))
        elif cfg.kind == "visca_preset":
            await driver.recall(value)
        elif cfg.kind == "http":
            path = cfg.path.replace("{value}", value) if cfg.method == "GET" else cfg.path
            try:
                async with httpx.AsyncClient(timeout=4, trust_env=False, follow_redirects=False) as client:
                    result = await client.request(cfg.method, f"http://{camera.ip}:{camera.control_port}{path}",
                                                  headers=cfg.headers, content=None if cfg.method == "GET" else value)
                    result.raise_for_status()
            except httpx.HTTPError:
                raise ValueError("Camera HTTP tracking request failed.") from None
        else:
            raise ValueError("Choose a tracking command first.")

    async def command(self, camera, cfg, enabled):
        for attempt in range(2):
            try:
                await asyncio.wait_for(self.send(camera, cfg, enabled), 6)
                return
            except (ValueError, OSError, TimeoutError):
                if attempt:
                    raise

    async def disarm(self, camera, force=False):
        async with self.locks[camera.id]:
            if camera.id in self.armed or force:
                self.armed.add(camera.id)
                await self.command(camera, self.config(camera), False)
                self.armed.discard(camera.id)
                self.active.pop(camera.id, None)
                self.runtime.follow.camera_frames.pop(camera.id, None)
                self.transition(camera.id, "WIDE", {})

    async def run(self, camera, seat, token, recall, current, decision_at, superseded):
        cid = camera.id
        async with self.locks[cid]:
            if not current():
                return {}
            active = self.active.get(cid)
            if cid in self.armed and active and active[:2] == (seat, token):
                if not camera.handoff_reanchor_s or time.monotonic()-active[2] < camera.handoff_reanchor_s:
                    return {"settle_ms": 0}
            cfg = self.config(camera)
            trace = {"decision": decision_at}
            if camera.handoff_mode == "dry":
                for state in ("DISARMING", "MOVING", "SETTLING", "WIDE" if seat == "wide" else "ARMING"):
                    self.transition(cid, state, trace)
                self.runtime.follow.audit(f"Dry-run handoff {cid}: OFF, preset {token}, settle, " + ("stay OFF" if seat == "wide" else "ON"))
                return {"simulated": True}
            if not camera.control_enabled or not self.record(camera).get("verified"):
                raise ValueError("Verify both tracking commands on this camera first.")
            try:
                if cid in self.armed:
                    self.active.pop(cid, None)
                    self.transition(cid, "DISARMING", trace)
                    await self.command(camera, cfg, False)
                    trace["off_ack"] = time.monotonic()
                    gap = self.record(camera).get("profile", {}).get("off_recall_gap_ms", 0)
                    if gap:
                        try:
                            await asyncio.wait_for(superseded.wait(), gap/1000)
                        except TimeoutError:
                            pass
                    self.armed.discard(cid)
                if not current():
                    return {}
                self.transition(cid, "MOVING", trace)
                try:
                    result = await asyncio.wait_for(recall(cid, token), 20)
                except (ValueError, OSError, TimeoutError):
                    if not current():
                        return {}
                    result = await asyncio.wait_for(recall(cid, token), 20)
                trace["recall_ack"] = time.monotonic()
                self.transition(cid, "SETTLING", trace)
                if result.get("settle_ms") is None:
                    position = camera.preset_positions.get(token)
                    if position:
                        await asyncio.wait_for(self.driver(camera).settled(position), 16)
                    else:
                        profile = self.record(camera).get("profile", {})
                        delay = profile.get("travel_ms", {}).get(token, cfg.settle_ms)
                        driver = self.driver(camera)
                        waits = [asyncio.create_task(superseded.wait())]
                        if driver.completion:
                            waits.append(asyncio.create_task(driver.wait_for_completion()))
                        try:
                            done, _ = await asyncio.wait(waits, timeout=delay/1000,
                                                         return_when=asyncio.FIRST_COMPLETED)
                            for task in done:
                                task.result()
                        finally:
                            for task in waits:
                                task.cancel()
                            await asyncio.gather(*waits, return_exceptions=True)
                if not current():
                    return {}
                if seat != "wide":
                    self.transition(cid, "ARMING", trace)
                    self.armed.add(cid)  # An ACK can be lost after the device executes ON.
                    await self.command(camera, cfg, True)
                    trace["on_ack"] = time.monotonic()
                    if not current():
                        await self.command(camera, cfg, False)
                        self.armed.discard(cid)
                        return {}
                self.transition(cid, "WIDE" if seat == "wide" else "TRACKING", trace)
                if seat == "wide":
                    self.active.pop(cid, None)
                else:
                    self.active[cid] = (seat, token, time.monotonic())
                values = self.samples[cid]
                values.append((time.monotonic()-decision_at)*1000)
                self.states[cid].update(median_ms=round(median(values),1), p90_ms=round(sorted(values)[(9*len(values)-1)//10],1))
                return {**result, "settle_ms": (time.monotonic()-trace["SETTLING"])*1000}
            except (ValueError, OSError, TimeoutError):
                self.transition(cid, "ERROR_RECOVERY", trace)
                self.armed.add(cid)
                await self.command(camera, cfg, False)
                self.armed.discard(cid)
                wide = self.runtime.settings.tracking.wide_presets.get(cid)
                if wide and current():
                    await asyncio.wait_for(recall(cid, wide), 20)
                raise
