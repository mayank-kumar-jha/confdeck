"""Local administrator setup and physical verification of optional handoff."""
import time

from confdeck.handoff import Control
from confdeck.visca import ViscaIpDriver


def add_handoff_routes(app, runtime):
    @app.get("/api/cameras/{identity}/handoff")
    async def read_handoff(identity: str):
        camera = next((c for c in runtime.settings.cameras if c.id == identity), None)
        if camera is None:
            raise ValueError("Select a configured camera.")
        record = runtime.follow.handoffs.record(camera)
        cfg = Control.model_validate_json(record["config"]) if record.get("config") else Control()
        return {"config": cfg.model_dump(exclude={"headers"}),
                "headers_saved": bool(cfg.headers),
                "protected": camera.protected_presets, "prep": camera.prep_camera,
                "silence_s": camera.handoff_silence_s, "reanchor_s": camera.handoff_reanchor_s}

    @app.post("/api/cameras/{identity}/handoff")
    async def configure_handoff(identity: str, body: dict):
        camera = next((c for c in runtime.settings.cameras if c.id == identity), None)
        if camera is None:
            raise ValueError("Select a configured camera.")
        hand = runtime.follow.handoffs
        action = body.get("action")
        async with runtime.write_lock:
            if action == "pause":
                resume_follow = runtime.follow.live
                runtime.follow.stop("Tracking handoff paused")
                await hand.disarm(camera)
                if hand.record(camera).get("test_pending"):
                    runtime.secrets.set("handoff-" + identity, **{**hand.record(camera), "test_pending": False})
                camera.handoff_mode = "off"
                runtime.save()
                if resume_follow:
                    runtime.follow.mode(True)
                return {"ok": True}
            if action in ("on", "travel", "recall_on") and hand.armed - {identity}:
                raise ValueError("Finish the OFF test on the other camera first.")
            if runtime.follow.live or identity in runtime.manual.leases:
                raise ValueError("Stop Follow and release the joystick before setup.")
            async with hand.locks[identity]:
                key = "handoff-" + identity
                if action == "save":
                    if identity in hand.armed or camera.handoff_mode == "live":
                        raise ValueError("Pause this handoff before replacing commands.")
                    cfg = Control.model_validate(body.get("config", {}))
                    if body.get("keep_headers") is True:
                        cfg.headers = hand.config(camera).headers
                    slots = body.get("protected", [80, 81])
                    if not isinstance(slots, list) or any(type(n) is not int or not 0 <= n <= 127 for n in slots):
                        raise ValueError("Protected presets must be numbers from 0 to 127.")
                    if cfg.kind == "visca_preset":
                        slots = slots + [int(cfg.on), int(cfg.off)]
                        assigned = [runtime.settings.tracking.wide_presets, *(z.presets for z in runtime.settings.tracking.zones)]
                        if any(m.get(identity) in (cfg.on, cfg.off) for m in assigned):
                            raise ValueError("A tracking command preset is already assigned to a seat or wide shot.")
                    camera.handoff_silence_s = body.get("silence_s",10)
                    camera.handoff_reanchor_s = body.get("reanchor_s",0)
                    camera.prep_camera = body.get("prep") is True
                    camera.protected_presets = sorted(set([80, 81, *slots]))
                    hand.trials.pop(identity, None)
                    runtime.secrets.set(key, ip=camera.ip, ports=[camera.control_port,camera.visca_port], config=cfg.model_dump_json(), verified=False)
                    camera.handoff_mode = "off"
                elif action in ("on", "off"):
                    if not body.get("confirm") or not camera.control_enabled or camera.demo or camera.control_protocol != "visca":
                        raise ValueError("Enable and verify VISCA control, then confirm this physical test.")
                    cfg = hand.config(camera)
                    await ViscaIpDriver(camera).check()
                    trial = hand.trials.setdefault(identity, {})
                    if action == "on":
                        trial.clear()
                        runtime.secrets.set(key, **{**hand.record(camera), "verified": False})
                    trial["sent"] = time.monotonic()
                    runtime.secrets.set(key, **{**hand.record(camera), "test_pending": True})
                    hand.armed.add(identity)
                    await hand.command(camera, cfg, action == "on")
                    if action == "off":
                        hand.armed.discard(identity)
                        runtime.secrets.set(key, **{**hand.record(camera), "test_pending": False})
                    trial["awaiting"] = action
                elif action == "observe":
                    trial = hand.trials.get(identity, {})
                    observed = trial.pop("awaiting", None)
                    if not observed or body.get("observed") != observed:
                        raise ValueError("Run the corresponding command test first.")
                    trial[observed] = round((time.monotonic()-trial["sent"])*1000)
                    if observed == "off" and "on" in trial:
                        runtime.secrets.set(key, **{**hand.record(camera), "verified": True,
                                                   "observed_lock_ms": trial["on"]})
                elif action in ("travel", "recall_on", "observe_travel"):
                    record = hand.record(camera)
                    cfg = hand.config(camera)
                    if not record.get("verified") or not camera.control_enabled or camera.handoff_mode != "off":
                        raise ValueError("Verify ON/OFF, pause handoff and enable VISCA before measuring.")
                    trial = hand.trials.setdefault(identity, {})
                    if action == "observe_travel":
                        measurement = trial.pop("measurement", None)
                        if measurement is None:
                            raise ValueError("Start a travel measurement first.")
                        token, start, while_on, gap, origin = measurement
                        elapsed = round((time.monotonic()-start)*1000)
                        await hand.command(camera, cfg, False)
                        hand.armed.discard(identity)
                        if not 100 <= elapsed <= 15000:
                            raise ValueError("Repeat the measurement and confirm within 15 seconds.")
                        profile = dict(record.get("profile", {}))
                        if while_on:
                            profile.update(recall_while_on=body.get("moved") is True,
                                           recall_stops_tracking=body.get("stopped") is True)
                        elif body.get("moved") is True:
                            times = profile.setdefault("travel_ms", {})
                            times[token] = max(times.get(token, 0), elapsed)
                            profile["off_recall_gap_ms"] = min(gap, profile.get("off_recall_gap_ms", gap))
                            if origin is not None and token in camera.preset_positions:
                                distance = max(abs(a-b) for a,b in zip(origin,camera.preset_positions[token]))
                                profile.setdefault("by_distance", {})[str(distance)] = elapsed
                        runtime.secrets.set(key, **{**record, "profile": profile, "test_pending":False})
                    else:
                        token = str(body.get("token", ""))
                        if (body.get("confirm") is not True or not token.isdigit()
                                or token not in camera.preset_records or int(token) in camera.protected_presets
                                or not camera.preset_start <= int(token) <= camera.preset_end):
                            raise ValueError("Confirm a saved, reserved seat preset for this test.")
                        driver = ViscaIpDriver(camera)
                        await driver.check()
                        gap = body.get("gap_ms", 0)
                        if type(gap) is not int or not 0 <= gap <= 3000:
                            raise ValueError("Choose an OFF-to-recall gap from 0 to 3000 ms.")
                        try:
                            origin = await driver.position()
                        except (OSError, ValueError):
                            origin = None
                        runtime.secrets.set(key, **{**record, "test_pending":True})
                        hand.armed.add(identity)
                        try:
                            await hand.command(camera, cfg, action == "recall_on")
                            if action == "travel" and gap:
                                import asyncio
                                await asyncio.sleep(gap/1000)
                            start = time.monotonic()
                            await driver.recall(token)
                        except BaseException:
                            await hand.command(camera, cfg, False)
                            hand.armed.discard(identity)
                            raise
                        trial["measurement"] = (token, start, action == "recall_on", gap, origin)
                elif action == "mode":
                    cfg = hand.config(camera)
                    mode = body.get("mode")
                    if mode not in ("live","dry") or cfg.kind == "none" or identity in hand.armed:
                        raise ValueError("Complete the OFF test before choosing a handoff mode.")
                    if mode == "live":
                        if not hand.record(camera).get("verified") or not camera.control_enabled:
                            raise ValueError("Test and visually confirm both ON and OFF first.")
                        await ViscaIpDriver(camera).check()
                        await hand.command(camera, cfg, False)
                    camera.handoff_mode = mode
                else:
                    raise ValueError("Unknown handoff action.")
                runtime.save()
                return {"ok": True, "mode": camera.handoff_mode,
                        "verified": bool(hand.record(camera).get("verified"))}
