"""Live lab state. Disk writes and heavy fitting/replay run outside the event loop."""
import asyncio
import time

from .session import Recorder


class AccuracyLab:
    def __init__(self, runtime):
        self.runtime = runtime
        from .seat_sample import SeatSample
        self.seat_sample = SeatSample(runtime)
        self.recorder = None
        self.session = None
        self.start_time = 0
        self.trial = None
        self.vision = {}
        self.scores = {}
        self.moving_until = {}
        self.vision_warning = ""
        self.last_frame = {}
        self.mouth = None
        self.mouth_failed = False
        self.autotrack_camera = None
        self.autotrack_driver = None
        self.autotrack_driving = False
        self.last_autotrack_time = 0.0
        self.autotrack_drivers = {}
        self.autotrack_driving_cams = {}
        self.last_autotrack_times = {}
        self.post_preset_targets = {}
        self.active_presets = {}
        self.last_frames = {}
        self._teaching_roi_anchors = {}

    def put(self, kind, **data):
        if self.recorder:
            t = time.monotonic()-self.start_time
            row = {"kind": kind, "t": t, **data}
            if len(self.session["rows"]) >= 50000 or t > 1800:
                self.vision_warning = "Session limit reached; stop and export"
                self.session["incomplete"] = True
                return
            self.session["rows"].append(row)
            self.recorder.put(kind, t, **data)

    def raw(self, obj, now):
        self.put("raw", raw=obj)
        level = obj.get("/api/audio/inputs/microphone/level")
        if level is not None:
            self.put("level", value=level)

    async def start(self, tag, tags):
        await self.stop()
        self.start_time = time.monotonic()
        self.recorder = Recorder(self.runtime.root / "accuracy-sessions", tag, tags,
                                 monotonic_start=self.start_time)
        self.session = {**self.recorder.meta, "rows": [], "labels": []}
        return self.session["id"]

    async def stop(self):
        if self.trial:
            self.stop_trial()
        if self.recorder:
            recorder, self.recorder = self.recorder, None
            try:
                await asyncio.to_thread(recorder.close)
            except ValueError:
                self.session["incomplete"] = True
                raise
            if recorder.dropped:
                self.session["incomplete"] = True

    def start_trial(self, seat, condition):
        if not self.recorder or self.trial:
            raise ValueError("Start a session and stop the previous trial first")
        self.trial = {"start": time.monotonic()-self.start_time, "seat": seat, "condition": condition}

    def stop_trial(self):
        if not self.trial:
            raise ValueError("No trial is running")
        label = {**self.trial, "end": time.monotonic()-self.start_time}
        self.session["labels"].append(label)
        self.put("label", **label)
        self.trial = None

    def mic(self, event, engine, decision):
        self.put("mic", event=event)
        self.put("decision", seat=engine.current, confidence=float(getattr(engine, "confidence", 0)),
                 reason=engine.reason, output=decision)

    def command(self, camera, preset, status, seat=None):
        self.put("camera", camera=camera, preset=preset, status=status)
        self.active_presets[camera] = str(preset or "")
        if status == "sent":
            self.moving_until[camera] = float("inf")
        elif status in ("accepted", "error"):
            self.moving_until[camera] = time.monotonic()+self.runtime.settings.tracking.settle_ms/1000+.5
            if status == "accepted" and str(preset or "").strip():
                self.notify_preset_recalled(camera, str(preset), seat=seat)

    def _find_seat_for_preset(self, cid: str, preset: str) -> str | None:
        if not preset:
            return None
        keys = self.camera_keys(cid)
        tracking = getattr(self.runtime.settings, "tracking", None)
        if not tracking:
            return None
        for k in keys:
            if str((getattr(tracking, "wide_presets", None) or {}).get(k, "")) == str(preset):
                return "wide"
        for z in getattr(tracking, "zones", []) or []:
            presets = getattr(z, "presets", None) or {}
            if any(str(presets.get(k, "")) == str(preset) for k in keys):
                return z.id
        return None

    def notify_preset_recalled(
        self,
        camera: str | None,
        preset: str = "",
        seat: str | None = None,
        settle_seconds: float | None = None,
    ) -> dict | None:
        cid = self.resolve_camera_id(camera)
        if not cid:
            return None
        now = time.monotonic()
        if settle_seconds is not None:
            self.set_camera_moving(cid, settle_seconds)
        preset_str = str(preset or "")
        if preset_str:
            self.active_presets[cid] = preset_str
        config = self.runtime.settings.tracking.vision
        if not getattr(config, "track_after_preset", True):
            return None
        resolved_seat = seat if seat is not None else self._find_seat_for_preset(cid, preset_str)
        if resolved_seat == "wide":
            self.post_preset_targets.pop(cid, None)
            return None
        keys = self.camera_keys(cid)
        matching_rois = [
            r for r in (config.rois or [])
            if r.camera in keys and (not preset_str or r.preset in ("", preset_str))
        ]
        teaching_roi = next((r for r in matching_rois if getattr(r, "kind", "seat") == "teaching"), None)
        seat_roi = next((r for r in matching_rois if resolved_seat and r.seat == resolved_seat), None)
        chosen_roi = teaching_roi if (teaching_roi and getattr(config, "teaching_zone_priority", True)) else seat_roi
        if not resolved_seat and chosen_roi:
            resolved_seat = chosen_roi.seat
        prev = self.post_preset_targets.get(cid, {})
        hold_s = float(getattr(config, "lock_hold_s", 3.0) or 3.0)
        entry = {
            "camera": cid,
            "seat": resolved_seat or "",
            "preset": preset_str,
            "mode": "teaching" if (teaching_roi and getattr(config, "teaching_zone_priority", True)) else "seat",
            "box": prev.get("box") if prev.get("seat") == resolved_seat else (list(chosen_roi.rect) if chosen_roi else None),
            "signature": list(chosen_roi.face_signature) if (chosen_roi and getattr(chosen_roi, "face_signature", None)) else None,
            "engaged_at": now,
            "last_seen": now,
            "hold_until": now + hold_s,
        }
        self.post_preset_targets[cid] = entry
        if hasattr(self, "_teaching_roi_anchors"):
            self._teaching_roi_anchors.pop(cid, None)
        proc = self.vision.get(cid)
        if proc is not None and getattr(proc, "group_selector", None) is not None:
            proc.group_selector.reset()
        self.ensure_worker()
        return dict(entry)

    def clear_post_preset_tracking(self, camera: str | None = None) -> None:
        if camera is None:
            self.post_preset_targets.clear()
            if hasattr(self, "_teaching_roi_anchors"):
                self._teaching_roi_anchors.clear()
            return
        cid = self.resolve_camera_id(camera)
        self.post_preset_targets.pop(cid, None)
        if hasattr(self, "_teaching_roi_anchors"):
            self._teaching_roi_anchors.pop(cid, None)

    def has_active_post_preset(self, camera: str | None) -> bool:
        if not camera:
            return False
        cid = self.resolve_camera_id(camera)
        config = self.runtime.settings.tracking.vision
        if not getattr(config, "track_after_preset", True):
            return False
        if cid not in self.post_preset_targets:
            return False
        if self.effective_camera_mode(cid, config) == "off" and not self.is_autotrack_camera(cid):
            return False
        return True

    def _tracking_driver_for(self, cid: str):
        if self.is_autotrack_camera(cid) and self.autotrack_driver is not None:
            return self.autotrack_driver
        drv = self.autotrack_drivers.get(cid)
        if drv is not None:
            return drv
        cam = next((c for c in (getattr(self.runtime.settings, "cameras", []) or []) if c.id == cid), None)
        if cam and getattr(cam, "control_enabled", False) and getattr(cam, "control_protocol", "none") == "visca" and not getattr(cam, "demo", False):
            from confdeck.visca import ViscaIpDriver

            drv = ViscaIpDriver(cam)
            self.autotrack_drivers[cid] = drv
            return drv
        return None

    @staticmethod
    def _s16(val: int) -> int:
        v = int(val)
        return v - 65536 if v >= 32768 else v

    async def _prepare_locked_teaching_rois(
        self,
        camera: str,
        preset: str,
        rois: dict,
        pixels,
        driver,
        now: float,
    ) -> tuple[dict, dict | None]:
        """Keep Teaching Zone ROIs anchored to the physical blackboard when PTZ moves."""
        teaching_items = [
            (s, r) for s, r in rois.items()
            if isinstance(r, dict) and str(r.get("kind") or "seat").strip().lower() in (
                "teaching", "blackboard", "board", "screen", "presenter", "teacher", "teaching_zone"
            )
        ]
        if not teaching_items:
            return rois, None

        seat_key, roi_dict = teaching_items[0]
        raw_rect = roi_dict.get("rect")
        if not isinstance(raw_rect, (list, tuple)) or len(raw_rect) < 4:
            return rois, None

        rx, ry, rw, rh = [float(v) for v in raw_rect[:4]]
        if max(rx, ry, rw, rh) <= 1.05:
            rx, ry, rw, rh = rx * 320.0, ry * 180.0, rw * 320.0, rh * 180.0
        base_rect = [round(rx, 2), round(ry, 2), round(rw, 2), round(rh, 2)]
        anchor_key = (str(preset or ""), seat_key, tuple(base_rect))

        if not hasattr(self, "_teaching_roi_anchors"):
            self._teaching_roi_anchors = {}
        anchor = self._teaching_roi_anchors.get(camera)
        if anchor is None or anchor.get("key") != anchor_key:
            anchor = {
                "key": anchor_key,
                "seat": seat_key,
                "base_rect": base_rect,
                "home_pos": None,
                "home_pos_raw": None,
                "curr_pos": None,
                "last_pos_t": 0.0,
                "px_per_pan": 0.55,
                "px_per_tilt": 0.55,
                "cam_pan_px": 0.0,
                "cam_tilt_px": 0.0,
                "prev_gray": None,
                "prev_gray_pos": None,
                "smoothed_box": None,
                "last_seen_t": now,
                "returned_home": False,
                "pan_px_min": min(-12.0, (rx - 160.0) - 18.0),
                "pan_px_max": max(12.0, (rx + rw - 160.0) + 18.0),
                "tilt_px_min": min(-10.0, (ry - 90.0) - 14.0),
                "tilt_px_max": max(10.0, (ry + rh - 90.0) + 14.0),
                "locked_rect": [int(round(rx)), int(round(ry)), int(round(rw)), int(round(rh))],
            }
            self._teaching_roi_anchors[camera] = anchor

        if driver is not None and hasattr(driver, "position"):
            should_poll = (anchor["home_pos"] is None) or (now - anchor["last_pos_t"] >= 0.20)
            if should_poll:
                try:
                    raw_pos = await asyncio.wait_for(driver.position(), timeout=0.06)
                    if isinstance(raw_pos, (tuple, list)) and len(raw_pos) >= 3:
                        s_pos = (self._s16(raw_pos[0]), self._s16(raw_pos[1]), int(raw_pos[2]))
                        if anchor["home_pos"] is None:
                            anchor["home_pos"] = s_pos
                            anchor["home_pos_raw"] = tuple(int(v) for v in raw_pos[:3])
                        anchor["curr_pos"] = s_pos
                        anchor["last_pos_t"] = now
                except Exception:
                    pass

        if anchor["home_pos"] is not None and anchor["curr_pos"] is not None:
            d_pan = anchor["curr_pos"][0] - anchor["home_pos"][0]
            d_tilt = anchor["curr_pos"][1] - anchor["home_pos"][1]

            try:
                import cv2
                import numpy as np

                arr = np.asarray(pixels, dtype=np.uint8)
                curr_g = arr if arr.ndim == 2 else cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
                if curr_g.shape[:2] == (180, 320):
                    if anchor["prev_gray"] is not None and anchor["prev_gray_pos"] is not None:
                        step_pan = anchor["curr_pos"][0] - anchor["prev_gray_pos"][0]
                        step_tilt = anchor["curr_pos"][1] - anchor["prev_gray_pos"][1]
                        if abs(step_pan) >= 12 or abs(step_tilt) >= 12:
                            shift, resp = cv2.phaseCorrelate(
                                anchor["prev_gray"].astype(np.float32),
                                curr_g.astype(np.float32),
                            )
                            if resp >= 0.25:
                                if abs(step_pan) >= 12 and abs(shift[0]) >= 3.0 and (step_pan * shift[0] < 0):
                                    est_x = abs(float(shift[0])) / abs(float(step_pan))
                                    if 0.15 <= est_x <= 3.0:
                                        anchor["px_per_pan"] = anchor["px_per_pan"] * 0.65 + est_x * 0.35
                                if abs(step_tilt) >= 12 and abs(shift[1]) >= 2.0 and (step_tilt * shift[1] > 0):
                                    est_y = abs(float(shift[1])) / abs(float(step_tilt))
                                    if 0.15 <= est_y <= 3.0:
                                        anchor["px_per_tilt"] = anchor["px_per_tilt"] * 0.65 + est_y * 0.35
                    if (
                        anchor["prev_gray"] is None
                        or abs(anchor["curr_pos"][0] - anchor["prev_gray_pos"][0]) >= 12
                        or abs(anchor["curr_pos"][1] - anchor["prev_gray_pos"][1]) >= 12
                    ):
                        anchor["prev_gray"] = curr_g.copy()
                        anchor["prev_gray_pos"] = anchor["curr_pos"]
            except Exception:
                pass

            cam_pan_px = d_pan * anchor["px_per_pan"]
            cam_tilt_px = -d_tilt * anchor["px_per_tilt"]
            anchor["cam_pan_px"] = cam_pan_px
            anchor["cam_tilt_px"] = cam_tilt_px

            sx1 = max(0.0, min(304.0, rx - cam_pan_px))
            sy1 = max(0.0, min(164.0, ry - cam_tilt_px))
            sx2 = max(sx1 + 16.0, min(320.0, (rx + rw) - cam_pan_px))
            sy2 = max(sy1 + 16.0, min(180.0, (ry + rh) - cam_tilt_px))
            locked_rect = [int(round(sx1)), int(round(sy1)), int(round(sx2 - sx1)), int(round(sy2 - sy1))]
            anchor["locked_rect"] = locked_rect

            locked_rois = dict(rois)
            updated_roi = dict(roi_dict)
            updated_roi["rect"] = locked_rect
            if isinstance(roi_dict.get("masks"), list):
                shifted_masks = []
                for m in roi_dict["masks"]:
                    if isinstance(m, (list, tuple)) and len(m) >= 4:
                        mx, my, mw, mh = [float(v) for v in m[:4]]
                        shifted_masks.append([
                            int(round(mx - cam_pan_px)),
                            int(round(my - cam_tilt_px)),
                            int(round(mw)),
                            int(round(mh)),
                        ])
                updated_roi["masks"] = shifted_masks
            locked_rois[seat_key] = updated_roi
            return locked_rois, anchor

        return rois, anchor

    def _select_tracking_target(self, cid: str, rois: dict, processor, config, now: float):
        """Choose target bounding box honoring Teaching Zone mode and Post-Preset hysteresis."""
        faces_dict = dict(getattr(processor, "detected_face_boxes", {}) or {})
        poses_dict = dict(getattr(processor, "target_poses", {}) or {})
        speaking_seats = [s for s, spk in (getattr(processor, "speaking_state", {}) or {}).items() if spk]
        all_faces = list(getattr(processor, "all_faces", []) or [])
        rejected = dict(getattr(processor, "binding_rejected", {}) or {})
        hold_s = float(getattr(config, "lock_hold_s", 3.0) or 3.0)
        lock = self.post_preset_targets.get(cid)

        def _box_dist(b1, b2) -> float:
            cx1, cy1 = b1[0] + b1[2] * 0.5, b1[1] + b1[3] * 0.5
            cx2, cy2 = b2[0] + b2[2] * 0.5, b2[1] + b2[3] * 0.5
            return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5

        # Mode 1: Mapped Teaching / Blackboard Zone priority (front + back-of-head/body)
        teaching_seats = [s for s, r in rois.items() if r.get("kind") == "teaching"]
        if teaching_seats and getattr(config, "teaching_zone_priority", True):
            for s in teaching_seats:
                if s in speaking_seats and s in faces_dict and not rejected.get(s):
                    box = faces_dict[s]
                    if lock is not None:
                        lock.update({"seat": s, "mode": "teaching", "box": list(box), "last_seen": now, "hold_until": now + hold_s})
                    return box, poses_dict.get(s, "front"), s, "teaching_speaking"
            for s in teaching_seats:
                if s in faces_dict and not rejected.get(s):
                    box = faces_dict[s]
                    if lock is not None:
                        lock.update({"seat": s, "mode": "teaching", "box": list(box), "last_seen": now, "hold_until": now + hold_s})
                    return box, poses_dict.get(s, "back_or_body"), s, f"teaching_{poses_dict.get(s, 'present')}"
            # Hold inside teaching zone: NEVER jump to unrelated students outside teaching ROI
            return None, "missing", teaching_seats[0], "teaching_hold"

        group = getattr(processor, "group_speaker", None) or {}
        group_box = group.get("target_box")
        group_reason = group.get("reason") or ""

        # Mode 2: Locked Seat / Post-Preset Continuous Tracking with hysteresis
        if lock is not None:
            locked_seat = lock.get("seat") or ""
            if group_box is not None and group_reason == "lip_sync_locked" and len(all_faces) > 1:
                lock.update({"box": list(group_box), "last_seen": now, "hold_until": now + hold_s})
                return group_box, "front", locked_seat, "lip_sync_locked"
            if locked_seat and locked_seat in faces_dict and not rejected.get(locked_seat):
                box = faces_dict[locked_seat]
                lock.update({"box": list(box), "last_seen": now, "hold_until": now + hold_s})
                return box, poses_dict.get(locked_seat, "front"), locked_seat, "seat_lock"

            # If speaker leaned slightly outside seat ROI box (or ROI not drawn for that preset),
            # track nearest face within proximity of prior lock box without jumping across room
            prev_box = lock.get("box")
            if all_faces and prev_box is not None and not rejected.get(locked_seat):
                nearest = min(all_faces, key=lambda f: _box_dist(f, prev_box))
                max_jump = max(65.0, float(prev_box[2]) * 2.2)
                if _box_dist(nearest, prev_box) <= max_jump:
                    lock.update({"box": list(nearest), "last_seen": now, "hold_until": now + hold_s})
                    return nearest, "front", locked_seat, "seat_continuity"

            if now < float(lock.get("hold_until", 0.0)):
                return None, "hold", locked_seat, "hysteresis_hold"

            # After hysteresis expires on an autotrack-enabled camera, allow switching to verified speaking seat
            if self.is_autotrack_camera(cid):
                for s in speaking_seats:
                    if s in faces_dict and not rejected.get(s):
                        box = faces_dict[s]
                        lock.update({"seat": s, "box": list(box), "last_seen": now, "hold_until": now + hold_s})
                        return box, poses_dict.get(s, "front"), s, "handoff_speaker"

        # Fallback: General Autotrack mode (only when explicitly enabled on camera)
        if self.is_autotrack_camera(cid):
            if group_box is not None and group_reason == "lip_sync_locked":
                return group_box, "front", "", "lip_sync_locked"
            for s in speaking_seats:
                if s in faces_dict and not rejected.get(s):
                    return faces_dict[s], poses_dict.get(s, "front"), s, "speaking"
            for s, box in faces_dict.items():
                if not rejected.get(s):
                    return box, poses_dict.get(s, "front"), s, "present"
            if all_faces:
                if group_reason == "waiting_for_speaker" and len(all_faces) > 1:
                    return None, "", "", "waiting_for_speaker"
                box = max(all_faces, key=lambda f: f[2] * f[3])
                return box, "front", "", "largest_face"

        return None, "", "", "idle"

    def active_scores(self):
        config = self.runtime.settings.tracking.vision
        if config.mode != "active":
            return {}
        now = time.monotonic()
        result = {}
        for camera, (stamp, scores) in self.scores.items():
            if now-stamp < .6 and config.camera_modes.get(camera, config.mode) == "active":
                result.update(scores)
        return result

    def _vision_alias_store(self) -> dict[str, dict]:
        if not hasattr(self, "_camera_aliases"):
            self._camera_aliases = {}
        return self._camera_aliases

    def _discover_omnitrack_aliases(self) -> None:
        """Load OmniTrack camera_config.json / camera_rtsp.json if present locally."""
        import json
        import os
        from pathlib import Path

        now = time.monotonic()
        if now - getattr(self, "_omnitrack_probe_at", 0.0) < 10.0:
            return
        self._omnitrack_probe_at = now
        candidates = []
        if os.environ.get("OMNITRACK_DIR"):
            d = Path(os.environ["OMNITRACK_DIR"])
            candidates.extend([d, d / "backend_services"])
        if os.environ.get("CONFDECK_OMNITRACK_DIR"):
            d = Path(os.environ["CONFDECK_OMNITRACK_DIR"])
            candidates.extend([d, d / "backend_services"])
        if os.environ.get("CONFDECK_OMNITRACK_CAMERA_CONFIG"):
            candidates.append(Path(os.environ["CONFDECK_OMNITRACK_CAMERA_CONFIG"]).parent)
        candidates.extend([
            Path("/home/ordain/omnitrack-sennheiser/backend_services"),
            Path("/home/ordain/omnitrack-sennheiser"),
        ])
        base = next((p for p in candidates if (p / "camera_config.json").is_file() or (p / "camera_rtsp.json").is_file()), None)
        if base is None:
            return
        cfg_file = base / "camera_config.json"
        rtsp_file = base / "camera_rtsp.json"
        rtsp_map = {}
        if rtsp_file.is_file():
            try:
                loaded_rtsp = json.loads(rtsp_file.read_text(encoding="utf-8"))
                if isinstance(loaded_rtsp, dict):
                    rtsp_map = loaded_rtsp
            except Exception:
                rtsp_map = {}
        cfg_map = {}
        if cfg_file.is_file():
            try:
                raw_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(raw_cfg, dict):
                    inner = raw_cfg.get("camera_config")
                    cfg_map = inner if isinstance(inner, dict) else raw_cfg
            except Exception:
                cfg_map = {}
        if not cfg_map and not rtsp_map:
            return
        items = []
        for key in sorted(set(cfg_map.keys()) | set(rtsp_map.keys())):
            cfg_entry = cfg_map.get(key)
            rtsp_entry = rtsp_map.get(key)
            if not isinstance(cfg_entry, dict) and not isinstance(rtsp_entry, dict):
                continue
            cfg_entry = cfg_entry if isinstance(cfg_entry, dict) else {}
            rtsp_entry = rtsp_entry if isinstance(rtsp_entry, dict) else {}
            items.append(
                {
                    "key": key,
                    "name": cfg_entry.get("cam_name") or cfg_entry.get("name") or rtsp_entry.get("name") or key,
                    "ip": cfg_entry.get("ip") or "",
                    "protocol": cfg_entry.get("protocol") or cfg_entry.get("brand") or "",
                    "rtsp_url": cfg_entry.get("rtsp_url") or rtsp_entry.get("rtsp_url") or rtsp_entry.get("original") or "",
                    "local_rtsp": rtsp_entry.get("local") or "",
                }
            )
        if items:
            self.sync_cameras(items, create_missing=False)

    def sync_cameras(self, items: list[dict], create_missing: bool = True) -> dict:
        """Bind Port 4000 camera keys (`camera_1`, `omni_<ip>`) to ConfDeck camera IDs."""
        from urllib.parse import urlsplit
        from confdeck.models import Camera

        aliases = self._vision_alias_store()
        added = []
        updated = False
        cams = list(self.runtime.settings.cameras)
        for item in items or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("id") or "").strip()
            ip = str(item.get("ip") or "").strip()
            name = str(item.get("name") or key or ip).strip()
            local_rtsp = str(item.get("local_rtsp") or "").strip()
            rtsp_url = str(item.get("rtsp_url") or item.get("rtsp") or "").strip()
            if not key and not ip:
                continue
            cam = next(
                (
                    c
                    for c in cams
                    if (key and getattr(c, "id", "") == key)
                    or (ip and getattr(c, "ip", "") == ip)
                    or (ip and key == f"omni_{ip.replace('.', '_')}")
                    or (key and getattr(c, "ip", "") and key == f"omni_{getattr(c, 'ip', '').replace('.', '_')}")
                ),
                None,
            )
            if cam is None and create_missing and (ip or local_rtsp or rtsp_url):
                new_id = key or f"omni_{ip.replace('.', '_')}"
                rtsp_target = rtsp_url or local_rtsp
                parsed = urlsplit(rtsp_target) if rtsp_target else None
                port = (parsed.port if parsed and parsed.port else None) or 554
                path = ((parsed.path or "") + (f"?{parsed.query}" if parsed and parsed.query else "")) or "/rtpstream/config1"
                proto = "visca" if ("visca" in str(item.get("protocol", "")).lower() or ip.endswith(".128")) else "none"
                if ip or rtsp_target:
                    cam = Camera(
                        id=new_id,
                        name=name or new_id,
                        ip=ip or "127.0.0.1",
                        port=port,
                        path=path,
                        control_protocol=proto,
                        control_enabled=(proto == "visca"),
                    )
                    cams.append(cam)
                    added.append(new_id)
                    updated = True
            if cam is not None:
                cam_ip = getattr(cam, "ip", "")
                cam_port = getattr(cam, "port", 554) or 554
                cam_path = getattr(cam, "path", "") or "/rtpstream/config1"
                fallback_rtsp = f"rtsp://{cam_ip}:{cam_port}{cam_path}" if cam_ip else ""
                aliases[cam.id] = {
                    "id": cam.id,
                    "key": key or aliases.get(cam.id, {}).get("key", cam.id),
                    "ip": ip or cam_ip,
                    "name": name or getattr(cam, "name", cam.id),
                    "local_rtsp": local_rtsp or (f"rtsp://127.0.0.1:8554/{key}" if key else aliases.get(cam.id, {}).get("local_rtsp", "")),
                    "rtsp_url": rtsp_url or fallback_rtsp or aliases.get(cam.id, {}).get("rtsp_url", ""),
                }
        if updated and hasattr(self.runtime, "save"):
            self.runtime.settings.cameras = cams
            try:
                self.runtime.save()
            except Exception:
                pass
        return {"cameras": [getattr(c, "id", "") for c in cams], "added": added, "aliases": dict(aliases)}

    def resolve_camera_id(self, identifier: str | None) -> str:
        """Resolve canonical ConfDeck camera ID from id, IP, omni_<ip>, or Port 4000 camera_N key."""
        raw = str(identifier or "").strip()
        if not raw:
            return ""
        self._discover_omnitrack_aliases()
        cams = list(getattr(self.runtime.settings, "cameras", []) or [])
        aliases = self._vision_alias_store()
        for c in cams:
            if getattr(c, "id", "") == raw:
                return c.id
        for cid, info in aliases.items():
            if raw in (info.get("key"), info.get("ip"), cid):
                if any(getattr(c, "id", "") == cid for c in cams):
                    return cid
        for c in cams:
            cip = getattr(c, "ip", "")
            if cip and raw in (cip, f"omni_{cip.replace('.', '_')}"):
                return c.id
            if getattr(c, "name", "") == raw:
                return c.id
        if raw.startswith("omni_"):
            ip_guess = raw[5:].replace("_", ".")
            for c in cams:
                if getattr(c, "ip", "") == ip_guess:
                    return c.id
        return raw

    def camera_keys(self, identifier: str | None) -> set[str]:
        """Return canonical ID plus all known aliases for ROI, mode, and lease matching."""
        raw = str(identifier or "").strip()
        cid = self.resolve_camera_id(raw)
        keys = {k for k in (raw, cid) if k}
        info = self._vision_alias_store().get(cid, {})
        for k in (info.get("key"), info.get("ip")):
            if k:
                keys.add(k)
        for c in getattr(self.runtime.settings, "cameras", []) or []:
            cip = getattr(c, "ip", "")
            if getattr(c, "id", "") == cid and cip:
                keys.add(cip)
                keys.add(f"omni_{cip.replace('.', '_')}")
        return keys

    def effective_camera_mode(self, camera: str | None, config=None) -> str:
        cfg = config or self.runtime.settings.tracking.vision
        if cfg.mode == "off":
            return "off"
        for key in (self.resolve_camera_id(camera), *sorted(self.camera_keys(camera))):
            if key in cfg.camera_modes:
                return cfg.camera_modes[key]
        return cfg.mode

    def is_autotrack_camera(self, camera: str | None) -> bool:
        target = getattr(self, "autotrack_camera", None)
        if not target or not camera:
            return False
        return self.resolve_camera_id(target) in self.camera_keys(camera)

    def set_camera_moving(self, camera: str | None, seconds: float = 1.2) -> float:
        cid = self.resolve_camera_id(camera)
        secs = float(seconds)
        if secs <= 0:
            for k in self.camera_keys(cid):
                self.moving_until.pop(k, None)
            return 0.0
        until = time.monotonic() + max(0.05, min(secs, 10.0))
        for k in self.camera_keys(cid):
            self.moving_until[k] = max(self.moving_until.get(k, 0.0), until)
        return until

    def vision_state(self, camera: str | None = None) -> dict:
        states = getattr(self, "_vision_states", {})
        config = self.runtime.settings.tracking.vision
        autotrack_cam = self.resolve_camera_id(getattr(self, "autotrack_camera", "") or "")
        if camera:
            cid = self.resolve_camera_id(camera)
            entry = dict(states.get(cid, {}))
            cam_obj = next((c for c in self.runtime.settings.cameras if c.id == cid), None)
            native = bool(cam_obj and getattr(cam_obj, "native_tracking", False))
            alias_info = self._vision_alias_store().get(cid, {})
            entry.setdefault("camera", cid)
            entry["alias"] = alias_info.get("key") or entry.get("alias") or cid
            entry["mode"] = config.mode
            entry["camera_mode"] = self.effective_camera_mode(cid, config)
            entry.setdefault("scores", {})
            entry.setdefault("motion", {})
            entry.setdefault("occupancy", {})
            entry.setdefault("speaking", {})
            entry.setdefault("faces", {})
            entry.setdefault("all_faces", [])
            entry.setdefault("unassigned_faces", [])
            entry.setdefault("poses", {})
            entry.setdefault("binding_scores", {})
            entry.setdefault("binding_rejected", {})
            entry.setdefault("teaching_targets", {})
            entry.setdefault("tracking", {"active": False, "pose": "", "seat": "", "reason": "idle", "target_box": None, "post_preset": self.has_active_post_preset(cid)})
            entry.setdefault("frame_size", [320, 180])
            entry["autotrack"] = {
                "enabled": bool(autotrack_cam == cid or native or self.has_active_post_preset(cid)),
                "mode": "vision" if autotrack_cam == cid else ("native" if native else ("post_preset" if self.has_active_post_preset(cid) else "off")),
                "camera": cid,
                "post_preset": self.has_active_post_preset(cid),
                "locked_seat": (self.post_preset_targets.get(cid) or {}).get("seat", ""),
            }
            return entry
        return {
            "mode": config.mode,
            "autotrack_camera": autotrack_cam,
            "cameras": {
                c.id: self.vision_state(c.id)
                for c in (getattr(self.runtime.settings, "cameras", []) or [])
            },
        }

    def ensure_worker(self, loop=None) -> bool:
        """Start the headless RTSP vision capture loop once when an asyncio loop is active."""
        import asyncio
        import os
        import threading

        if os.environ.get("CONFDECK_DISABLE_VISION_WORKER") == "1":
            return False
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = getattr(self, "_loop", None)
        if loop is not None:
            self._loop = loop
        if getattr(self, "_worker_started", False):
            t = getattr(self, "_worker_thread", None)
            if t is not None and t.is_alive():
                return True
        if getattr(self, "_loop", None) is None:
            return False
        self._worker_stop = threading.Event()
        self._worker_started = True
        t = threading.Thread(target=self._vision_worker_loop, name="confdeck-vision-rtsp", daemon=True)
        self._worker_thread = t
        t.start()
        return True

    def stop_worker(self) -> None:
        stop_ev = getattr(self, "_worker_stop", None)
        if stop_ev is not None:
            stop_ev.set()
        self._worker_started = False

    def _select_rtsp_urls(self, cam) -> list[str]:
        info = self._vision_alias_store().get(cam.id, {})
        cip = getattr(cam, "ip", "")
        cport = getattr(cam, "port", 554) or 554
        cpath = getattr(cam, "path", "") or "/rtpstream/config1"
        direct_rtsp = f"rtsp://{cip}:{cport}{cpath}" if cip else ""
        alias_key = info.get("key") or ""
        raw_candidates = [
            f"rtsp://127.0.0.1:8554/{cam.id}",
            info.get("local_rtsp"),
            info.get("rtsp_url"),
            getattr(cam, "rtsp", ""),
            direct_rtsp,
        ]
        if alias_key and info:
            raw_candidates.append(f"http://127.0.0.1:4000/api/ptz/preview_feed?camera={alias_key}")
        urls = []
        for candidate in raw_candidates:
            text = str(candidate or "").strip()
            if text.startswith(("rtsp://", "rtsps://", "http://", "https://")) and text not in urls:
                urls.append(text)
        return urls

    def _select_rtsp_url(self, cam) -> str:
        urls = self._select_rtsp_urls(cam)
        return urls[0] if urls else ""

    def _vision_worker_loop(self) -> None:
        import asyncio
        import os

        try:
            import cv2
        except Exception:
            return

        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;1500000")
        caps = {}
        backoff_until = {}
        last_processed = {}
        url_index = {}
        stop_ev = getattr(self, "_worker_stop", None)

        try:
            while stop_ev is None or not stop_ev.is_set():
                loop = getattr(self, "_loop", None)
                if loop is None or loop.is_closed():
                    break
                now = time.monotonic()
                config = self.runtime.settings.tracking.vision
                cams = list(getattr(self.runtime.settings, "cameras", []) or [])
                last_client = getattr(self, "_last_client_frame", {})
                active_ids = set()
                did_work = False

                for cam in cams:
                    cid = cam.id
                    eff_mode = self.effective_camera_mode(cid, config)
                    is_auto = self.is_autotrack_camera(cid) or self.has_active_post_preset(cid)
                    if eff_mode == "off" and not is_auto:
                        continue
                    urls = self._select_rtsp_urls(cam)
                    if not urls:
                        continue
                    active_ids.add(cid)

                    # Defer seamlessly whenever a browser tab is pushing frames directly.
                    if now - last_client.get(cid, 0.0) < 1.5:
                        continue

                    has_rois = any(r.camera in self.camera_keys(cid) for r in (config.rois or []))
                    min_interval = 0.055 if is_auto else (0.10 if has_rois else 0.25)
                    if now - last_processed.get(cid, 0.0) < min_interval:
                        continue
                    if now < backoff_until.get(cid, 0.0):
                        continue

                    idx = url_index.get(cid, 0) % len(urls)
                    rtsp_url = urls[idx]
                    cap = caps.get(cid)
                    if cap is None or not cap.isOpened():
                        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                        try:
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        except Exception:
                            pass
                        if not cap.isOpened():
                            cap.release()
                            caps.pop(cid, None)
                            url_index[cid] = (idx + 1) % len(urls)
                            backoff_until[cid] = now + (0.25 if idx + 1 < len(urls) else 3.0)
                            continue
                        caps[cid] = cap

                    ok, bgr = cap.read()
                    if not ok or bgr is None:
                        cap.release()
                        caps.pop(cid, None)
                        url_index[cid] = (idx + 1) % len(urls)
                        backoff_until[cid] = now + (0.25 if idx + 1 < len(urls) else 1.5)
                        continue

                    small = cv2.resize(bgr, (320, 180), interpolation=cv2.INTER_AREA)
                    preset = ""
                    try:
                        preset = self.runtime.store.state.get("current_preset", {}).get(cid, "") if hasattr(self.runtime, "store") and isinstance(getattr(self.runtime.store, "state", None), dict) else ""
                    except Exception:
                        preset = ""
                    try:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.frame(cid, preset, small, quiet=False, source="rtsp", hi_res_frame=bgr),
                            loop,
                        )
                        fut.result(timeout=2.0)
                        last_processed[cid] = time.monotonic()
                        did_work = True
                    except Exception:
                        backoff_until[cid] = time.monotonic() + 1.0

                for cid in list(caps.keys()):
                    if cid not in active_ids:
                        try:
                            caps[cid].release()
                        except Exception:
                            pass
                        caps.pop(cid, None)

                if stop_ev is not None:
                    stop_ev.wait(0.015 if did_work else 0.15)
                else:
                    time.sleep(0.015 if did_work else 0.15)
        finally:
            for cap in caps.values():
                try:
                    cap.release()
                except Exception:
                    pass

    async def frame(self, camera, preset, pixels, quiet=False, source="client", hi_res_frame=None):
        import asyncio

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        camera = self.resolve_camera_id(camera)
        if source != "rtsp":
            if not hasattr(self, "_last_client_frame"):
                self._last_client_frame = {}
            self._last_client_frame[camera] = time.monotonic()
        config = self.runtime.settings.tracking.vision
        effective_mode = self.effective_camera_mode(camera, config)
        if (config.mode == "off" or effective_mode == "off") and not self.is_autotrack_camera(camera):
            return {"mode": "off", "scores": {}}
        from confdeck.vision.motion import MotionEnergy, llr
        now = time.monotonic()
        previous = self.last_frame.get(camera)
        self.last_frame[camera] = now
        if camera not in self.vision:
            try:
                from confdeck.vision.mouth import SeatVisionDetector
                self.vision[camera] = SeatVisionDetector(config.mouth_model or None)
            except Exception:
                self.vision[camera] = MotionEnergy()
        processor = self.vision[camera]
        if previous is not None and now-previous > 2:
            if hasattr(processor, "reset"):
                processor.reset()
            if getattr(processor, "disabled", False) and "lag" in getattr(processor, "warning", "").lower():
                processor.disabled = False
                processor.warning = ""
        cam_keys = self.camera_keys(camera)
        rois = {
            r.seat: (r.model_dump() if hasattr(r, "model_dump") else (vars(r) if hasattr(r, "__dict__") else dict(r)))
            for r in config.rois
            if r.camera in cam_keys and (r.preset == preset or not r.preset or preset == "")
        }
        blocked = any(now < self.moving_until.get(k, 0) for k in cam_keys) or any(
            k in self.runtime.manual.leases for k in cam_keys
        )
        should_track = self.is_autotrack_camera(camera) or self.has_active_post_preset(camera)
        driver = self._tracking_driver_for(camera) if (should_track and not blocked) else None
        teaching_anchor = None
        if should_track and not blocked:
            rois, teaching_anchor = await self._prepare_locked_teaching_rois(
                camera, preset, rois, pixels, driver, now
            )
        started = time.perf_counter()
        if hasattr(processor, "group_selector"):
            scores = await asyncio.to_thread(
                processor.process, pixels, rois, now, blocked, quiet, hi_res_frame
            )
        else:
            scores = await asyncio.to_thread(processor.process, pixels, rois, now, blocked, quiet)
        if config.mouth_model and not self.mouth_failed and scores and not blocked and not hasattr(processor, "last_llr"):
            try:
                if self.mouth is None:
                    from confdeck.vision.mouth import MouthMotion
                    self.mouth = await asyncio.to_thread(MouthMotion, config.mouth_model)
                candidate = self.runtime.follow.engine.candidate or self.runtime.follow.engine.current
                if candidate in rois:
                    import numpy as np
                    x, y, w, h = rois[candidate]["rect"]
                    value = await asyncio.to_thread(self.mouth.score, np.asarray(pixels)[y:y+h, x:x+w])
                    if value is not None:
                        scores[candidate] = value
            except (ImportError, ValueError, RuntimeError, OSError) as exc:
                self.mouth_failed = True
                self.vision_warning = f"Mouth model unavailable; motion fallback: {exc}"
        cost = time.perf_counter() - started
        if cost > 0.4:
            processor.warning = f"Vision frame took {int(cost*1000)}ms"
        autonomous_llrs = getattr(processor, "last_llr", {})
        calibrated = {
            seat: (llr(scores[seat], config.llr_models[seat]) if seat in config.llr_models
                   else autonomous_llrs.get(seat, 0.0))
            for seat in rois if seat in scores or seat in autonomous_llrs
        }
        self.scores[camera] = now, calibrated
        self.last_frames[camera] = pixels
        self.put("vision", camera=camera, scores=calibrated, motion=scores, mode=config.mode)
        self.vision_warning = processor.warning or self.vision_warning

        track_meta = {
            "active": False,
            "pose": "",
            "seat": "",
            "reason": "idle",
            "target_box": None,
            "locked_roi": list(teaching_anchor["locked_rect"]) if teaching_anchor else None,
            "post_preset": self.has_active_post_preset(camera),
        }
        if should_track and driver and not blocked:
            try:
                target_box, target_pose, locked_seat, lock_reason = self._select_tracking_target(
                    camera, rois, processor, config, now
                )
                is_teaching = lock_reason.startswith("teaching")

                # Smooth target bbox across frames in teaching mode to prevent body-box jitter
                # while keeping front-face tracking crisp and responsive.
                if target_box and is_teaching and teaching_anchor is not None:
                    prev_sb = teaching_anchor.get("smoothed_box")
                    if prev_sb is not None:
                        a = 0.72 if target_pose == "front" else 0.55
                        target_box = [
                            round(a * target_box[i] + (1.0 - a) * prev_sb[i], 2)
                            for i in range(4)
                        ]
                    teaching_anchor["smoothed_box"] = list(target_box)
                    teaching_anchor["last_seen_t"] = now
                    teaching_anchor["returned_home"] = False

                track_meta.update({
                    "active": True,
                    "pose": target_pose,
                    "seat": locked_seat,
                    "reason": lock_reason,
                    "target_box": [round(float(v), 1) for v in target_box] if target_box else None,
                })
                is_driving = bool(self.autotrack_driving_cams.get(camera, self.autotrack_driving if self.is_autotrack_camera(camera) else False))

                if target_box:
                    fx, fy, fw, fh = target_box
                    cx = fx + fw / 2.0
                    # Aim center of face when front-facing, upper-chest/head height for full body
                    cy = fy + fh * (0.45 if target_pose == "front" else (0.28 if is_teaching else (0.38 if target_pose == "back_or_body" else 0.48)))
                    dx = (cx - 160.0) / 160.0
                    dy = (cy - 90.0) / 90.0

                    # Schmitt-trigger deadband in teaching mode: avoid micro-chatter near center
                    if is_teaching:
                        stop_x, stop_y = 0.055, 0.065
                        enter_x, enter_y = (stop_x, stop_y) if is_driving else (0.10, 0.11)
                    else:
                        enter_x, enter_y = 0.07, 0.08

                    max_pan_speed = 0.32 if is_teaching else 1.0
                    max_tilt_speed = 0.24 if is_teaching else 1.0

                    # Gentle bounded zoom-in for teaching zone OR locked super-seat speaker
                    zoom_v = 0.0
                    if (
                        is_teaching
                        and teaching_anchor is not None
                        and teaching_anchor.get("home_pos") is not None
                        and teaching_anchor.get("curr_pos") is not None
                    ):
                        home_z = int(teaching_anchor["home_pos"][2])
                        curr_z = int(teaching_anchor["curr_pos"][2])
                        if curr_z < home_z + 1400 and fh < 72:
                            zoom_v = 0.18
                    elif not is_teaching and lock_reason == "lip_sync_locked":
                        group_sp = getattr(processor, "group_speaker", None) or {}
                        if group_sp.get("zoom_action") == "in":
                            zoom_v = 0.20
                            sel = getattr(processor, "group_selector", None)
                            if sel is not None:
                                sel.record_zoom_pulse(0.06, now)

                    last_t = self.last_autotrack_times.get(camera, self.last_autotrack_time if self.is_autotrack_camera(camera) else 0.0)
                    if abs(dx) <= enter_x and abs(dy) <= enter_y and zoom_v == 0.0:
                        if is_driving:
                            await driver.stop()
                            self.autotrack_driving_cams[camera] = False
                            if self.is_autotrack_camera(camera):
                                self.autotrack_driving = False
                    elif (now - last_t) >= 0.05:
                        if abs(dx) <= enter_x and abs(dy) <= enter_y:
                            pan_v, tilt_v = 0.0, 0.0
                        else:
                            sign_x = 1.0 if dx > 0 else -1.0
                            err_x = max(0.0, (abs(dx) - (0.05 if is_teaching else 0.07)) / (1.0 - (0.05 if is_teaching else 0.07)))
                            pan_v = sign_x * min(max_pan_speed, ((err_x ** 0.85) * 0.48) if is_teaching else ((err_x ** 0.65) * 1.30))

                            sign_y = -1.0 if dy > 0 else 1.0
                            err_y = max(0.0, (abs(dy) - (0.06 if is_teaching else 0.08)) / (1.0 - (0.06 if is_teaching else 0.08)))
                            tilt_v = sign_y * min(max_tilt_speed, ((err_y ** 0.85) * 0.38) if is_teaching else ((err_y ** 0.70) * 1.20))

                        # Hard blackboard ROI angular guardrail: never drive beyond locked blackboard edges
                        if is_teaching and teaching_anchor is not None and teaching_anchor.get("home_pos") is not None:
                            cp_x = float(teaching_anchor.get("cam_pan_px", 0.0))
                            cp_y = float(teaching_anchor.get("cam_tilt_px", 0.0))
                            if (pan_v > 0 and cp_x >= teaching_anchor["pan_px_max"]) or (pan_v < 0 and cp_x <= teaching_anchor["pan_px_min"]):
                                pan_v = 0.0
                            if (tilt_v > 0 and cp_y <= teaching_anchor["tilt_px_min"]) or (tilt_v < 0 and cp_y >= teaching_anchor["tilt_px_max"]):
                                tilt_v = 0.0

                        if abs(pan_v) < 0.03 and abs(tilt_v) < 0.03 and abs(zoom_v) < 0.05:
                            if is_driving:
                                await driver.stop()
                                self.autotrack_driving_cams[camera] = False
                                if self.is_autotrack_camera(camera):
                                    self.autotrack_driving = False
                        else:
                            self.last_autotrack_times[camera] = now
                            if self.is_autotrack_camera(camera):
                                self.last_autotrack_time = now
                            await driver.drive(pan=pan_v, tilt=tilt_v, zoom=zoom_v, speed=1.0)
                            self.autotrack_driving_cams[camera] = True
                            if self.is_autotrack_camera(camera):
                                self.autotrack_driving = True
                else:
                    if is_driving:
                        await driver.stop()
                        self.autotrack_driving_cams[camera] = False
                        if self.is_autotrack_camera(camera):
                            self.autotrack_driving = False
                    if is_teaching and teaching_anchor is not None:
                        teaching_anchor["smoothed_box"] = None
                        hold_s = float(getattr(config, "lock_hold_s", 3.0) or 3.0)
                        return_delay_s = min(hold_s, 1.2)
                        home_raw = teaching_anchor.get("home_pos_raw")
                        home_s = teaching_anchor.get("home_pos")
                        curr_s = teaching_anchor.get("curr_pos")
                        moved_from_home = (
                            home_s is not None
                            and curr_s is not None
                            and (
                                abs(curr_s[0] - home_s[0]) > 25
                                or abs(curr_s[1] - home_s[1]) > 25
                                or abs(curr_s[2] - home_s[2]) > 80
                            )
                        )
                        if (
                            home_raw is not None
                            and moved_from_home
                            and not teaching_anchor.get("returned_home")
                            and (now - float(teaching_anchor.get("last_seen_t", now))) >= return_delay_s
                            and hasattr(driver, "absolute")
                        ):
                            teaching_anchor["returned_home"] = True
                            self.set_camera_moving(camera, 1.5)
                            try:
                                await driver.absolute(home_raw, 0.25)
                            except Exception:
                                pass
            except Exception:
                pass

        result = {
            "scores": calibrated,
            "motion": scores,
            "occupancy": getattr(processor, "occupancy_state", {}),
            "speaking": getattr(processor, "speaking_state", {}),
            "faces": getattr(processor, "detected_face_boxes", {}),
            "all_faces": getattr(processor, "all_faces", []),
            "unassigned_faces": getattr(processor, "unassigned_faces", []),
            "poses": getattr(processor, "target_poses", {}),
            "binding_scores": getattr(processor, "binding_scores", {}),
            "binding_rejected": getattr(processor, "binding_rejected", {}),
            "teaching_targets": getattr(processor, "teaching_targets", {}),
            "tracking": track_meta,
            "warning": self.vision_warning,
            "mode": "off" if processor.disabled else config.mode,
            "camera_mode": effective_mode,
        }
        if not hasattr(self, "_vision_states"):
            self._vision_states = {}
        alias_info = self._vision_alias_store().get(camera, {})
        try:
            shape = np.asarray(pixels).shape
            frame_size = [int(shape[1]), int(shape[0])] if len(shape) >= 2 else [320, 180]
        except Exception:
            frame_size = [320, 180]
        self._vision_states[camera] = {
            **result,
            "camera": camera,
            "alias": alias_info.get("key") or camera,
            "preset": preset,
            "source": source,
            "blocked": bool(blocked),
            "frame_size": frame_size,
            "updated_at": time.monotonic(),
            "wall_time": time.time(),
        }
        return result

    def reset_vision(self):
        if self.autotrack_driving and self.autotrack_driver:
            try:
                asyncio.create_task(self.autotrack_driver.stop())
            except Exception:
                pass
            self.autotrack_driving = False
        for cam_id, drv in list(self.autotrack_drivers.items()):
            if self.autotrack_driving_cams.get(cam_id):
                try:
                    asyncio.create_task(drv.stop())
                except Exception:
                    pass
                self.autotrack_driving_cams[cam_id] = False
        self.post_preset_targets.clear()
        if hasattr(self, "_teaching_roi_anchors"):
            self._teaching_roi_anchors.clear()
        self.vision.clear()
        self.scores.clear()
        self.last_frame.clear()
        self.last_frames.clear()
        if self.mouth:
            self.mouth.close()
        self.mouth = None
        self.mouth_failed = False

    def snapshot(self):
        return {"recording": bool(self.recorder), "session_id": self.session["id"] if self.session else None,
                "tag": self.session.get("tag") if self.session else None,
                "trial": self.trial, "elapsed": time.monotonic()-self.start_time if self.recorder else 0,
                "dropped": self.recorder.dropped if self.recorder else 0,
                "error": self.recorder.error if self.recorder else None, "vision_warning": self.vision_warning}
