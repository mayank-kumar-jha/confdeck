"""Live/dry-run orchestration, bounded recordings, learning and isolated replay."""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from statistics import median

from confdeck.evaluation.lab import AccuracyLab
from confdeck.evaluation.replay import make_engine
from confdeck.ptz import CameraControl
from confdeck.visca import ViscaIpDriver

log = logging.getLogger(__name__)


class Follow:
    def __init__(self, runtime):
        self.runtime = runtime
        from confdeck.handoff import Handoffs
        self.handoffs = Handoffs(runtime)
        self.engine = make_engine(runtime.settings.tracking, runtime.settings.tracking.engine, speech=runtime.speech_enabled)
        self.lab = AccuracyLab(runtime)
        self.live = False
        self.log = deque(maxlen=200)
        self.errors = 0
        self.task = None
        self.generation = 0
        self.recording = None
        self.record_started = 0
        self.record = []
        self.preview_events = deque(maxlen=1200)
        self.preview_outcomes = deque(maxlen=1200)
        self.replay_task = None
        self.replay_state = None
        self.learn = None
        self.last_reason = ""
        self.actual_moves = deque()
        self.timings = deque(maxlen=20)
        self.camera_frames = {}
        self.superseded = asyncio.Event()
        self.pending_decision = None
        self.wake = asyncio.Event()
        self.conversation_current = None
        self.conversation_camera = None
        self.conversation_changed = 0
        self.far_end_was_blocked = False
        self.audit("Dry-run ready; camera control is off")

    def far_end_blocked(self):
        monitor = getattr(self.runtime, 'far_end', None)
        blocked = bool(monitor and monitor.snapshot()['blocked'])
        if blocked != self.far_end_was_blocked:
            self.engine = make_engine(self.runtime.settings.tracking, self.runtime.settings.tracking.engine,
                                      speech=self.runtime.speech_enabled)
            self.pending_decision = None
            self.far_end_was_blocked = blocked
            if hasattr(self.runtime, "set_mic_led_state"):
                self.runtime.background(self.runtime.set_mic_led_state(blocked))
        return blocked

    def audit(self, reason):
        self.log.append({"time": time.time(), "message": reason})
        log.info("Tracking: %s", reason)

    def configure(self, settings):
        self.stop("Settings changed; review in dry-run")
        self.engine = make_engine(settings, settings.engine, speech=self.runtime.speech_enabled)
        shadow = getattr(self.runtime, "fusion_shadow", None)
        if shadow:
            try:
                shadow.configure(settings)
            except Exception as error:
                shadow.status = "error"
                self.runtime.fusion_shadow_error = f"Shadow configuration changed: {type(error).__name__}: {error}"
                log.warning("%s", self.runtime.fusion_shadow_error)
        self.conversation_current = None
        self.lab.reset_vision()

    def stop(self, reason="Stopped following"):
        self.live = False
        self.camera_frames.clear()
        self.pending_decision = None
        self.conversation_current = self.conversation_camera = None
        self.conversation_changed = 0
        self.generation += 1
        if self.task:
            self.task.cancel()
        for camera in self.runtime.settings.cameras:
            if camera.id in self.handoffs.armed:
                self.runtime.background(self.handoffs.disarm(camera))
        self.audit(reason + "; no further recalls will be sent")

    def mode(self, live):
        if live:
            shadow = getattr(self.runtime, "fusion_shadow", None)
            if shadow and shadow.output_mode == "active":
                raise ValueError("Active fusion requires the OmniTrack bridge; ConfDeck live control must remain off.")
            if self.replay_task and not self.replay_task.done():
                raise ValueError("Stop replay before following live.")
            adapters = list(self.runtime.microphones.values()) or ([self.runtime.mic] if self.runtime.mic else [])
            if not any(m.status().get("age") is not None and m.status()["age"] <= 3 for m in adapters):
                raise ValueError("Connect the microphone and wait for fresh position data first.")
            s = self.runtime.settings.tracking
            if not s.zones or not any(z.presets for z in s.zones):
                raise ValueError("Add a zone and assign a camera preset first.")
            for mapping in [s.wide_presets, *(z.presets for z in s.zones)]:
                for cid, token in mapping.items():
                    self.camera(cid, token)
        self.stop("Follow live enabled" if live else "Dry-run enabled")
        self.live = live
        self.errors = 0
        self.engine = make_engine(self.runtime.settings.tracking, self.runtime.settings.tracking.engine, speech=self.runtime.speech_enabled)

    def camera(self, cid, token):
        c = next((c for c in self.runtime.settings.cameras if c.id == cid), None)
        if not c or not c.control_enabled or c.control_protocol == "none" or c.demo:
            raise ValueError("Every assigned camera needs an enabled control switch and a supported protocol.")
        if c.control_protocol in ("canon", "visca"):
            lo, hi = (1, 100) if c.control_protocol == "canon" else (0, 127)
            if not token.isascii() or not token.isdigit() or not lo <= int(token) <= hi:
                raise ValueError(f"{c.name}: preset must be {lo}–{hi}.")
        return c

    async def recall(self, cid, token):
        self.camera_frames.pop(cid, None)
        self.lab.command(cid, token, "sent")
        try:
            result = await self._recall(cid, token)
        except BaseException:
            self.lab.command(cid, token, "error")
            raise
        self.lab.command(cid, token, "accepted")
        return result

    async def _recall(self, cid, token):
        if cid in self.runtime.manual.leases:
            raise ValueError("Manual control is active for this camera.")
        camera = self.camera(cid, token)
        lock = self.runtime.preset_locks.setdefault(cid, asyncio.Lock())
        if lock.locked():
            raise ValueError("A camera command is already pending.")
        async with lock:
            if camera.control_protocol == "visca":
                driver = self.handoffs.driver(camera) if camera.handoff_mode == "live" else ViscaIpDriver(camera)
                return await driver.recall(token)
            async with CameraControl(camera, self.runtime.secrets.get(cid)) as control:
                speed = self.runtime.settings.tracking.preset_speed if camera.control_protocol == "canon" else None
                return await control.execute("recall", token=token, speed=speed)

    def dispatch(self, decision):
        if not decision:
            return
        if self.far_end_blocked():
            return
        if decision["zone"] != "wide":
            row = {"seat": decision["zone"], "decision_at": time.monotonic(), "evidence_ms": max(0, (decision["time"]-decision.get("evidence_start", self.engine.candidate_since))*1000), "cameras": {}}
            self.timings.append(row)
            decision["timing"] = row
            self.audit(f"Seat lock {row['seat']}: {row['evidence_ms']:.0f} ms evidence to decision")
        if not self.live:
            for cid, preset in decision["presets"].items():
                camera = next((c for c in self.runtime.settings.cameras if c.id == cid), None)
                if camera and camera.handoff_mode == "dry":
                    self.audit(f"Dry-run handoff {cid}: OFF, preset {preset}, settle, " + ("stay OFF" if decision["zone"] == "wide" else "ON"))
                self.audit(f"Dry-run: {decision['zone']} → {cid} preset {preset}")
            return
        if self.task and not self.task.done():
            self.superseded.set()
            self.pending_decision = decision
            self.audit("Latest seat queued until pending camera commands finish")
            return
        self.pending_decision = None
        self.superseded = asyncio.Event()
        settings = self.runtime.settings.tracking
        if settings.conversation_layout:
            from confdeck.conversation import plan
            seat = decision['zone']
            available = {c.id for c in self.runtime.settings.cameras if c.control_enabled
                         and c.control_protocol != 'none' and not c.demo and c.id not in self.runtime.manual.leases}
            if seat == 'wide':
                target = next(((cid, token) for cid, token in decision['presets'].items()
                               if cid in available), None)
            else:
                prep = {c.id for c in self.runtime.settings.cameras if c.prep_camera and c.handoff_mode == "live"
                        and c.id in available and c.id not in self.runtime.settings.display.cameras}
                assignment = plan(settings.zones, [seat], prep, self.camera_frames,
                                  settings.presenter_seat, settings.presenter_camera)
                if not assignment:
                    assignment = plan(settings.zones, [seat], available, self.camera_frames,
                                      settings.presenter_seat, settings.presenter_camera)
                target = assignment.get(seat)
            if target is None:
                self.audit(f"No available saved framing for {seat}")
                self.engine.current = self.engine.last_target = None
                return
            cid, token = target
            decision = {**decision, 'presets': {cid: token}, 'display_cameras': [cid],
                        'camera_seats': {cid: seat}}
        else:
            active_cids = [cid for cid in decision.get('presets', {}) if cid not in self.runtime.manual.leases]
            if active_cids:
                decision = {**decision, 'display_cameras': active_cids[:3]}
        self.task = self.runtime.background(self.execute(decision, self.generation))
        self.task.add_done_callback(lambda _: self.wake.set())

    async def execute(self, decision, generation):
        if not self.live or generation != self.generation:
            return

        presets = dict(decision["presets"])
        for camera in self.runtime.settings.cameras:
            if camera.handoff_mode == "dry" and camera.id in presets:
                token = presets.pop(camera.id)
                await self.handoffs.run(camera, decision["zone"], token, self.recall, lambda: True,
                                        time.monotonic(), self.superseded)
        if not presets:
            return
        decision = {**decision, "presets": presets}
        succeeded = set()
        async def recall_one(cid, token, seat):
            if not self.live or generation != self.generation:
                return
            if cid in self.runtime.manual.leases:
                self.audit(f"Manual control: skipped {cid}")
                return
            if decision.get('camera_seats') and self.camera_frames.get(cid) == (seat, token):
                succeeded.add(cid)
                return
            try:
                while self.actual_moves and time.monotonic()-self.actual_moves[0] >= 60:
                    self.actual_moves.popleft()
                if len(self.actual_moves) >= self.runtime.settings.tracking.max_moves_per_min:
                    self.audit('Camera move rate limit reached')
                    return
                self.actual_moves.append(time.monotonic())
                started = time.monotonic()
                timing = {"send_ms": (started-decision["timing"]["decision_at"])*1000} if "timing" in decision else {}
                if "timing" in decision:
                    decision["timing"]["cameras"][cid] = timing
                camera = next((c for c in self.runtime.settings.cameras if c.id == cid), None)
                if camera and camera.handoff_mode != "off":
                    def current():
                        return self.live and self.generation == generation and not self.pending_decision and cid not in self.runtime.manual.leases and cid not in self.handoffs.manual_pending
                    result = await self.handoffs.run(camera, seat, token, self.recall, current,
                                                     decision.get("timing", {}).get("decision_at", started), self.superseded)
                    if result.get("simulated") or not current():
                        return
                else:
                    result = await self.recall(cid, token)
                if hasattr(self.lab, "notify_preset_recalled"):
                    self.lab.notify_preset_recalled(cid, str(token), seat=seat)
                if decision.get('camera_seats') and result.get('settle_ms') is None:
                    await asyncio.sleep(self.runtime.settings.tracking.settle_ms/1000)
                if not self.live or generation != self.generation:
                    return
                self.camera_frames[cid] = (seat, token)
                succeeded.add(cid)
                timing["reply_ms"] = result.get("reply_ms", (time.monotonic()-started)*1000)
                timing["settle_ms"] = result.get("settle_ms")
                self.audit(f"Moved to {seat} → {cid} preset {token} (command accepted)")
            except (ValueError, OSError) as exc:
                self.errors += 1
                self.audit(f"Camera error: {exc}")
                if self.errors >= 3:
                    self.stop("Automatically stopped after 3 camera errors")
                self.engine.current = None
                self.engine.last_target = None

        display = self.runtime.settings.display
        for camera in self.runtime.settings.cameras:
            if camera.id in self.handoffs.armed and camera.id not in decision["presets"]:
                voice = self.engine.last_voice
                if decision["zone"] == "wide" and voice is not None and (not camera.handoff_silence_s
                        or time.monotonic()-voice < camera.handoff_silence_s):
                    continue
                try:
                    await self.handoffs.disarm(camera)
                except (ValueError, OSError):
                    self.stop("Could not verify tracker OFF")
                    return
        if decision.get('camera_seats'):
            target, token = next(iter(decision['presets'].items()))
            if self.conversation_changed and decision['zone'] not in ('wide', self.conversation_current):
                hold_until = self.conversation_changed + self.runtime.settings.tracking.min_hold_ms / 1000
                remaining = hold_until - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    if self.pending_decision or not self.live or generation != self.generation:
                        return
            needs_move = self.camera_frames.get(target) != (decision['zone'], token)
            if target in display.cameras and needs_move and decision['zone'] != 'wide':
                bridge = next(((cid, preset) for cid, preset in self.runtime.settings.tracking.wide_presets.items()
                               if cid != target and cid not in self.runtime.manual.leases), None)
                if bridge:
                    cid, preset = bridge
                    await recall_one(cid, preset, 'wide')
                    if cid not in succeeded or cid in self.runtime.manual.leases:
                        self.engine.current = self.engine.last_target = None
                        return
                    display.cameras, display.layout = [cid], 'single'
                    self.conversation_camera, self.conversation_current = cid, 'wide'
                    # Allow two 200 ms display updates before moving the previous camera.
                    await asyncio.sleep(.4)
                    if self.pending_decision:
                        return
                    if cid in self.runtime.manual.leases:
                        self.engine.current = self.engine.last_target = None
                        return
        await asyncio.gather(*(recall_one(cid, token, decision['zone']) for cid, token in decision["presets"].items()))
        if self.live and generation == self.generation and decision.get('display_cameras') and not self.pending_decision:
            shown = [cid for cid in decision['display_cameras'] if cid in succeeded and cid not in self.runtime.manual.leases]
            if not shown:
                self.engine.current = self.engine.last_target = None
                return
            display.cameras = shown
            display.layout = {1:'single', 2:'two', 3:'three'}.get(len(shown), 'single')
            self.conversation_camera = shown[0]
            self.conversation_current = decision['zone']
            self.conversation_changed = time.monotonic()

    def feed(self, event):
        now = time.monotonic()
        if self.far_end_blocked():
            return
        if event.get("mic_id", "primary") != "primary" and not hasattr(self.engine, "sources"):
            return  # An uncalibrated satellite must not drive the primary seat map.
        if self.recording:
            if len(self.record) >= 10000 or now - self.record_started >= 600:
                self.recording = False
                self.audit("Recording stopped at 10 minutes or 10,000 samples; export the session")
            else:
                self.record.append({"t": now - self.record_started, "event": event})
        self.preview_events.append({"t": now, "event": dict(event)})
        if hasattr(self.lab, "seat_sample"):
            try:
                self.lab.seat_sample.feed(dict(event), now)
            except Exception:
                pass
        while self.preview_events and now-self.preview_events[0]["t"] > 60:
            self.preview_events.popleft()
        shadow = getattr(self.runtime, "fusion_shadow", None)
        if shadow and shadow.status == "running":
            try:
                recommendation, frame = shadow.feed(event, now)
                if frame is not None:
                    self.lab.put("speech_frame", mic_id=event.get("mic_id", "primary"),
                                 reliability=frame["reliability"], no_signal=bool(frame.get("no_signal")),
                                 clipping=bool(frame.get("clipping")))
                self.lab.put("fusion_shadow", **shadow.engine.snapshot(now), recommendation=recommendation)
            except Exception as error:
                shadow.status = "error"
                self.runtime.fusion_shadow_error = f"Shadow stopped: {type(error).__name__}: {error}"
                log.warning("%s", self.runtime.fusion_shadow_error)
        if self.runtime.speech_enabled:
            if self.runtime.settings.tracking.engine != "new":
                self.engine.reject("speech mode requires spherical engine", True)
                return
            timestamp = event.get("sample_time", now)
            speech = self.runtime.speech_evidence(timestamp) if isinstance(timestamp, (int, float)) else None
            if speech is not None and not speech.get("no_signal"):
                event = {**event, "speech": speech}
        evidence = self.lab.active_scores() if self.runtime.settings.tracking.vision.mode == "active" else None
        decision = self.engine.feed({**event, "vision_llr": evidence} if evidence else event, now)
        self.preview_outcomes.append({"time": now, "mic_id": event.get("mic_id", "primary"), "azimuth": event.get("azimuth"), "elevation": event.get("elevation"), "accepted": self.engine.last_sample == now, "reason": self.engine.reason})
        self.lab.mic(event, self.engine, decision)
        self.dispatch(decision)
        if self.engine.reason != self.last_reason:
            self.last_reason = self.engine.reason
            self.audit(self.last_reason)
        if self.learn and event.get("mic_id", "primary") == self.learn["source"]:
            tracker = getattr(self.engine, "sources", {}).get(self.learn["source"], self.engine)
            if now <= self.learn["end"] and tracker.last_sample == now:
                self.learn["points"].append((event["azimuth"], event["elevation"]))

    def update_conversation_display(self, now):
        if not self.live or not self.runtime.settings.tracking.conversation_layout:
            return
        for cid in self.runtime.manual.leases:
            self.camera_frames.pop(cid, None)
        if self.task and not self.task.done():
            return
        if self.pending_decision:
            pending, self.pending_decision = self.pending_decision, None
            self.dispatch(pending)
            return
        if self.conversation_camera:
            display = self.runtime.settings.display
            display.cameras = [self.conversation_camera] if self.conversation_camera in self.camera_frames else []
            display.layout = 'single'

    async def run(self):
        while True:
            speech_ready = (not self.far_end_blocked() and
                            (not self.runtime.speech_enabled or self.runtime.speech_status()["healthy"]))
            if not speech_ready:
                self.pending_decision = None
                self.engine.current = None
                self.engine.candidate = None
            if speech_ready and self.pending_decision and not self.runtime.settings.tracking.conversation_layout and not (self.task and not self.task.done()):
                pending, self.pending_decision = self.pending_decision, None
                self.dispatch(pending)
            if speech_ready:
                self.update_conversation_display(time.monotonic())
                self.dispatch(self.engine.tick(time.monotonic()))
                self.handoffs.tick(self, time.monotonic())
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=.1)
            except TimeoutError:
                pass
            self.wake.clear()

    async def learn_zone(self, source="primary"):
        if self.live:
            raise ValueError("Switch to dry-run before learning a seat.")
        if self.learn:
            raise ValueError("A seat is already being learned.")
        self.learn = {"end": time.monotonic() + 20, "points": [], "source": source}
        self.audit("Learning seat: speak softly, normally, then loudly for 20 seconds")
        try:
            await asyncio.sleep(20)
            points = self.learn["points"]
            if len(points) < 10:
                raise ValueError("Not enough fresh speech positions. Check microphone/gates and try again.")
            from confdeck.tracking_math.geometry import angles, vector
            from confdeck.tracking_math.model import fit
            convention = self.runtime.settings.tracking.inference.convention
            components = await asyncio.to_thread(fit, [vector(a, e, convention) for a, e in points])
            a, e = angles(components[0]["mean"], convention)
            return {"id": uuid.uuid4().hex[:12], "mic_id": source, "azimuth": round(a, 1), "elevation": round(e, 1),
                    "radius": 25, "components": components}
        finally:
            self.learn = None

    def snapshot(self):
        now = time.monotonic()
        while self.actual_moves and now - self.actual_moves[0] >= 60:
            self.actual_moves.popleft()
        summary = {}
        for key in ("evidence_ms", "send_ms", "reply_ms", "settle_ms"):
            values = [r[key] for r in self.timings] if key == "evidence_ms" else [v[key] for r in self.timings for v in r["cameras"].values() if v.get(key) is not None]
            summary[key] = {"median": median(values), "worst": max(values)} if values else None
        shadow = getattr(self.runtime, "fusion_shadow", None)
        try:
            shadow_state = shadow.snapshot(now) if shadow else {"mode": "off", "status": "not configured", "error": getattr(self.runtime, "fusion_shadow_error", None)}
        except Exception as error:
            shadow_state = {"mode": "shadow", "status": "error", "error": f"{type(error).__name__}: {error}"}
        tracking = self.engine.snapshot(now)
        if shadow and shadow.output_mode == "active":
            controller = shadow.controller
            healthy = shadow.status == "running" and shadow_state.get("status") == "running"
            selected = controller.current if healthy else None
            zone = next((z for z in self.runtime.settings.tracking.zones if z.id == selected), None)
            tracking = {**tracking, "current": selected, "candidate": controller.pending,
                        "reason": controller.reason if healthy else "fusion unavailable / hold view",
                        "active_mic": zone.mic_id if zone else None}
        return {**tracking, "fusion_shadow": shadow_state, "live": self.live, "handoffs": self.handoffs.states, "log": list(self.log), "timing": {"last20": list(self.timings), "summary": summary},
                "events": [r for r in self.preview_outcomes if now-r["time"] <= 60][-200:],
                "event_stats": {"rate": sum(now-r["time"] <= 30 for r in self.preview_outcomes)/30, "rejected": sum(now-r["time"] <= 30 and not r["accepted"] for r in self.preview_outcomes)},
                "actual_moves_per_min": len(self.actual_moves), "recording": bool(self.recording),
                "record_count": len(self.record), "replay": self.replay_state,
                "learning": max(0, self.learn["end"] - now) if self.learn else 0}

    def start_record(self):
        self.record, self.recording, self.record_started = [], True, time.monotonic()
        self.audit("Recording raw microphone events")

    async def replay(self, rows, speed):
        self.stop("Replay started in isolated dry-run")
        engine = make_engine(self.runtime.settings.tracking.model_copy(deep=True), self.runtime.settings.tracking.engine)
        elapsed = 0.0
        decisions = []
        try:
            for row in rows:
                target = row["t"]
                # Tick gaps so return-to-wide works with no samples in the recording.
                while elapsed + .1 < target:
                    elapsed += .1
                    d = engine.tick(elapsed)
                    if d:
                        decisions.append(d)
                    self.replay_state = {**engine.snapshot(elapsed), "time": elapsed, "running": True, "decisions": decisions[-200:]}
                    await asyncio.sleep(.1 / speed)
                await asyncio.sleep(max(0, target - elapsed) / speed)
                elapsed = target
                d = engine.feed({**row["event"], "time": target, "sample_time": target}, target)
                if d:
                    decisions.append(d)
                d = engine.tick(target)
                if d:
                    decisions.append(d)
                self.replay_state = {**engine.snapshot(elapsed), "time": elapsed, "running": True, "decisions": decisions[-200:]}
            self.audit(f"Replay complete: {len(decisions)} simulated decisions; no camera commands")
        finally:
            if self.replay_state:
                self.replay_state["running"] = False


def validate_recording(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("Recording must contain 1–10,000 samples.")
    previous = -1
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("event"), dict):
            raise ValueError("Invalid recording sample.")
        t = row.get("t")
        if type(t) not in (float, int) or not previous < t <= 600:
            raise ValueError("Recording timestamps must increase and stay within 10 minutes.")
        previous = t
    # Reject non-finite values and oversized event payloads.
    if len(json.dumps(rows, allow_nan=False)) > 4_000_000:
        raise ValueError("Recording exceeds 4 MB.")
    return rows
