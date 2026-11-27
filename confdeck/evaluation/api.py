"""Local-admin evaluation endpoints; all analysis is offline and camera-free."""
import asyncio
import base64
import copy
import json

from fastapi import HTTPException
from fastapi.responses import FileResponse

from confdeck.tracking import TrackingSettings

from .assistants import propose_exclusions, settings_ab
from .metrics import export, metrics
from .protocol import CONDITIONS, protocol
from .replay import replay
from .session import validate
from .sweep import sweep
from .synthetic import generate


def add_accuracy_routes(app, runtime):
    lab = runtime.follow.lab
    lock = asyncio.Lock()
    ab_state, ab_next = {'running': False}, asyncio.Event()
    lab.ab_task = None

    def session(body, scoring=True):
        value = body.get('session') or lab.session
        if not value:
            raise ValueError('Record or import a labelled session first')
        if lab.recorder and value is lab.session:
            raise ValueError('Stop recording before analysis')
        value = copy.deepcopy(value)
        value['labels'] = value.get('labels') or [r for r in value['rows'] if r.get('kind') == 'label']
        if not value['labels']:
            raise ValueError('Ground truth labels are required')
        labels = sorted(value['labels'], key=lambda x: x['start'])
        if any(a['end'] > b['start'] for a, b in zip(labels, labels[1:])):
            raise ValueError('Ground truth intervals must not overlap')
        return validate(value, scoring)

    runtime.accuracy_status = lambda: {**lab.snapshot(), 'ab': ab_state}

    @app.get('/api/accuracy/seat-sample')
    async def seat_sample_status():
        return lab.seat_sample.snapshot()

    @app.post('/api/accuracy/seat-sample')
    async def seat_sample(body: dict):
        action = body.get('action')
        if action == 'start':
            return lab.seat_sample.start(body.get('name'), body.get('mic_id', 'primary'))
        if action == 'cancel':
            return lab.seat_sample.cancel()
        if action == 'save':
            async with runtime.write_lock:
                sample = lab.seat_sample.snapshot()
                if sample['status'] != 'ready':
                    raise ValueError('Complete a usable seat sample first')
                if runtime.follow.live:
                    raise ValueError('Turn Follow off before saving a seat')
                proposal = sample['proposal']
                if not lab.seat_sample.configuration_matches():
                    raise ValueError('Microphone or tracking settings changed; sample the seat again')
                sources = {'primary', *(m.id for m in runtime.settings.microphones)}
                if not {proposal['mic_id'], *proposal.get('microphone_profiles', {})} <= sources:
                    raise ValueError('The sampled microphone is no longer configured')
                settings = TrackingSettings.model_validate({**runtime.settings.tracking.model_dump(),
                    'zones': [*(z.model_dump() for z in runtime.settings.tracking.zones), proposal]})
                runtime.follow.configure(settings)
                runtime.settings.tracking = settings
                runtime.save()
                lab.seat_sample.cancel()
                return {'saved': proposal}
        raise ValueError('Choose start, cancel or save')

    @app.get('/api/accuracy/status')
    async def status():
        return {**lab.snapshot(), 'tracking': runtime.follow.snapshot(), 'ab': ab_state}

    @app.post('/api/accuracy/session')
    async def record(body: dict):
        if body.get('action') == 'start':
            if lab.seat_sample.snapshot()['status'] == 'recording':
                raise ValueError('Finish or discard the seat sample first')
            runtime.follow.stop('Accuracy Lab recording in dry-run')
            await lab.start(body.get('tag', 'test'), body.get('tags', []))
        elif body.get('action') == 'stop':
            await lab.stop()
        else:
            raise ValueError('Choose start or stop')
        return lab.snapshot()

    @app.get('/api/accuracy/session')
    async def download_session():
        if lab.recorder:
            raise ValueError('Stop recording before export')
        return lab.session

    @app.post('/api/accuracy/trial')
    async def trial(body: dict):
        if body.get('action') == 'stop':
            lab.stop_trial()
        else:
            if body.get('seat') not in [z.id for z in runtime.settings.tracking.zones]+[None] or body.get('condition') not in CONDITIONS:
                raise ValueError('Choose a configured seat and condition')
            lab.start_trial(body.get('seat'), body['condition'])
        return lab.snapshot()

    @app.get('/api/accuracy/protocol')
    async def plan(seed: int = 7):
        return protocol([z.id for z in runtime.settings.tracking.zones], seed)

    def compare_sync(data, settings, vision):
        reports, results = {}, {}
        for engine in ('legacy', 'new'):
            result = replay(data, settings, engine, vision)
            report = metrics(data, result, settings)
            report['provenance'] = str(data.get('id', 'imported'))
            reports[engine], results[engine] = report, result
            export(report, result, runtime.root / 'accuracy-reports' / engine)
        keys = ('correct_rate', 'wrong_rate', 'no_decision_rate', 'opposite_rate', 'switch_latency_median', 'spurious_switches_per_min', 'echo_false_trigger_rate')
        delta = {k: reports['new'][k]-reports['legacy'][k] if all(reports[e][k] is not None for e in reports) else None for k in keys}
        warnings = (['Shared-seat live operation requires the spherical engine and independent audio. '
                     'Legacy is shown only as an offline comparison; missing recorded speech fails closed in spherical replay.']
                    if any(z.microphone_profiles for z in settings.zones) else [])
        return {'reports': reports, 'new_minus_legacy': delta, 'warnings': warnings,
                'timeline': {e: r['points'][::max(1, len(r['points'])//1000)] for e, r in results.items()}}

    @app.post('/api/accuracy/compare')
    async def compare(body: dict):
        data, settings = generate() if body.get('synthetic') else (session(body), runtime.settings.tracking.model_copy(deep=True))
        mode = body.get('vision', 'off')
        if mode not in ('off', 'advisory', 'active'):
            raise ValueError('Invalid vision mode')
        async with lock:
            return await asyncio.to_thread(compare_sync, data, settings, mode)

    @app.get('/api/accuracy/report/{engine}/{format}')
    async def report(engine: str, format: str):
        if engine not in ('legacy', 'new') or format not in ('html', 'csv', 'json'):
            raise HTTPException(404)
        path = runtime.root / 'accuracy-reports' / f'{engine}.{format}'
        if not path.is_file():
            raise HTTPException(404, 'Run a comparison first')
        return FileResponse(path, filename=path.name)

    @app.post('/api/accuracy/sweep')
    async def search(body: dict):
        data, settings = session(body), runtime.settings.tracking.model_copy(deep=True)
        async with lock:
            return await asyncio.to_thread(sweep, data, settings, body.get('grid', {'confirm_ms': [600, 1000, 1500]}), body.get('limit', .3))

    @app.post('/api/accuracy/latency-sweep')
    async def search_latency(body: dict):
        from .latency import latency_sweep
        data, settings = session(body), runtime.settings.tracking.model_copy(deep=True)
        async with lock:
            result = await asyncio.to_thread(latency_sweep, data, settings)
            path = runtime.root / 'accuracy-reports' / 'latency-profiles.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, indent=2), encoding='utf-8')
            return result

    @app.post('/api/accuracy/profile')
    async def profile(body: dict):
        if not body.get('confirm'):
            raise ValueError('Confirm the suggested profile before applying')
        changes = body.get('profile', {})
        if set(changes)-{'confirm_ms', 'min_hold_ms', 'silence_ms', 'inference', 'engine', 'vision', 'conversation_layout', 'conversation_hold_ms'}:
            raise ValueError('Profile contains unsupported fields')
        settings = TrackingSettings.model_validate({**runtime.settings.tracking.model_dump(), **changes})
        async with runtime.write_lock:
            runtime.follow.configure(settings)
            runtime.settings.tracking = settings
            runtime.save()
        return settings.model_dump()

    @app.post('/api/accuracy/calibrate')
    async def calibration(body: dict):
        from confdeck.tracking_math.model import calibrate
        data = session(body, False)
        async with lock:
            result = await asyncio.to_thread(calibrate, data, runtime.settings.tracking.zones, runtime.settings.tracking.inference.convention)
        if body.get('apply'):
            settings = TrackingSettings.model_validate({**runtime.settings.tracking.model_dump(), 'zones': result['zones']})
            async with runtime.write_lock:
                runtime.follow.configure(settings)
                runtime.settings.tracking = settings
                runtime.save()
        return result

    @app.post('/api/accuracy/nadir')
    async def nadir(body: dict):
        from confdeck.tracking_math.geometry import nadir_check
        data = session(body, False)
        points = [(r['event']['azimuth'], r['event']['elevation']) for r in data['rows'] if r.get('kind') == 'mic']
        return nadir_check(points, runtime.settings.tracking.inference.convention)

    @app.post('/api/accuracy/offset')
    async def offset(body: dict):
        from confdeck.tracking_math.geometry import estimate_offset
        return {'suggested_offset': estimate_offset(body['observed'], body['expected']), 'requires_confirmation': True}

    @app.post('/api/accuracy/exclusions')
    async def exclusions(body: dict):
        if body.get('apply'):
            return await runtime.device_settings.apply(body['changes'], confirmed=body.get('confirm', False))
        return propose_exclusions(session(body))

    @app.post('/api/accuracy/ab')
    async def ab(body: dict):
        if body.get('action') == 'next':
            if not ab_state['running'] or not lab.recorder:
                raise ValueError('No A/B trial is running')
            await lab.stop()
            session({})
            ab_next.set()
            return ab_state
        if body.get('action') == 'cancel':
            if lab.ab_task:
                lab.ab_task.cancel()
                await asyncio.gather(lab.ab_task, return_exceptions=True)
            return ab_state
        profiles = body.get('profiles', [])
        if ab_state['running'] or not body.get('confirm') or not 2 <= len(profiles) <= 3:
            raise ValueError('Review and confirm 2-3 profiles; only one A/B experiment can run')
        runtime.follow.stop('Microphone settings A/B test')

        async def run_trial(profile):
            ab_next.clear()
            ab_state.update(profile=profile, index=ab_state.get('index', 0)+1)
            await lab.start('test', ['settings-ab', str(ab_state['index'])])
            await asyncio.wait_for(ab_next.wait(), timeout=1800)
            async with lock:
                return await asyncio.to_thread(compare_sync, session({}), runtime.settings.tracking.model_copy(deep=True), 'off')

        async def run_ab():
            ab_state.clear()
            ab_state.update(running=True, index=0)
            try:
                ab_state['result'] = await settings_ab(runtime.device_settings, profiles, run_trial, confirmed=True)
            except (ValueError, OSError, asyncio.TimeoutError) as exc:
                ab_state['error'] = str(exc)
            finally:
                await lab.stop()
                ab_state['running'] = False
        ab_state['running'] = True
        lab.ab_task = runtime.background(run_ab())
        return {'running': True}

    @app.post('/api/accuracy/vision/frame')
    async def frame(body: dict):
        camera = lab.resolve_camera_id(body.get('camera'))
        config = runtime.settings.tracking.vision
        if lab.effective_camera_mode(camera, config) == 'off' and not lab.is_autotrack_camera(camera) and not lab.has_active_post_preset(camera):
            return {'mode': 'off', 'scores': {}}
        if camera not in {c.id for c in runtime.settings.cameras}:
            raise ValueError('Unknown camera')
        body = {**body, 'camera': camera}
        if len(body.get('pixels', '')) > 76800:
            raise ValueError('Frame too large')
        raw = base64.b64decode(body.get('pixels', ''), validate=True)
        if len(raw) != 320*180:
            raise ValueError('Expected 320x180 grayscale bytes')
        import numpy as np
        async with lock:
            return await lab.frame(camera, body.get('preset', ''), np.frombuffer(raw, dtype=np.uint8).reshape(180, 320), bool(body.get('quiet')))

    @app.post('/api/accuracy/vision/calibrate')
    async def vision_calibrate(body: dict):
        data = session(body, False)
        if data['tag'] != 'calibration':
            raise ValueError('Vision likelihoods require separate calibration data')
        from confdeck.vision.motion import learn_llr
        models = {}
        for seat in [z.id for z in runtime.settings.tracking.zones]:
            speaking, silent = [], []
            for row in data['rows']:
                if row.get('kind') != 'vision' or seat not in row.get('motion', {}):
                    continue
                label = next((label for label in data['labels'] if label['start'] <= row['t'] < label['end']), None)
                if label and label['condition'] != 'overlap':
                    (speaking if label['seat'] == seat else silent).append(row['motion'][seat])
            models[seat] = learn_llr(speaking, silent)
        return {'llr_models': models, 'requires_confirmation': True}

    @app.post('/api/accuracy/vision/moved')
    async def moved(body: dict):
        if runtime.settings.tracking.vision.mode == 'off':
            return {'mode': 'off', 'moved': None}
        import numpy as np

        from confdeck.vision.motion import phase_correlation
        frames = []
        for name in ('before', 'after'):
            raw = base64.b64decode(body[name], validate=True)
            if len(raw) != 320*180:
                raise ValueError('Expected 320x180 frames')
            frames.append(np.frombuffer(raw, dtype=np.uint8).reshape(180, 320))
        return await asyncio.to_thread(phase_correlation, *frames)

    if hasattr(runtime, "start") and not getattr(runtime.start, "_vision_wrapped", False):
        _orig_runtime_start = runtime.start

        async def _start_with_vision():
            await _orig_runtime_start()
            lab.ensure_worker()

        _start_with_vision._vision_wrapped = True
        runtime.start = _start_with_vision

    if hasattr(runtime, "close") and not getattr(runtime.close, "_vision_wrapped", False):
        _orig_runtime_close = runtime.close

        async def _close_with_vision():
            lab.stop_worker()
            await _orig_runtime_close()

        _close_with_vision._vision_wrapped = True
        runtime.close = _close_with_vision

    def _serialize_roi(roi) -> dict:
        if hasattr(roi, "model_dump"):
            return roi.model_dump()
        if hasattr(roi, "__dict__"):
            return dict(vars(roi))
        return dict(roi)

    @app.get('/api/accuracy/vision/cameras')
    async def get_vision_cameras():
        lab._discover_omnitrack_aliases()
        lab.ensure_worker()
        config = runtime.settings.tracking.vision
        aliases = lab._vision_alias_store()
        autotrack_cam = lab.resolve_camera_id(getattr(lab, "autotrack_camera", "") or "")
        cameras_out = []
        for cam in runtime.settings.cameras:
            info = aliases.get(cam.id, {})
            native = bool(getattr(cam, "native_tracking", False))
            cam_ip = getattr(cam, "ip", "")
            cam_port = getattr(cam, "port", 554) or 554
            cam_path = getattr(cam, "path", "") or "/rtpstream/config1"
            cam_rtsp = info.get("rtsp_url") or getattr(cam, "rtsp", "") or (f"rtsp://{cam_ip}:{cam_port}{cam_path}" if cam_ip else "")
            cameras_out.append({
                "id": cam.id,
                "alias": info.get("key") or cam.id,
                "name": info.get("name") or cam.name,
                "ip": cam_ip or info.get("ip", ""),
                "rtsp": cam_rtsp,
                "local_rtsp": info.get("local_rtsp", ""),
                "mode": lab.effective_camera_mode(cam.id, config),
                "native_tracking": native,
                "autotrack_mode": "vision" if autotrack_cam == cam.id else ("native" if native else "off"),
                "rois": [
                    _serialize_roi(r)
                    for r in (config.rois or [])
                    if r.camera in lab.camera_keys(cam.id)
                ],
            })
        return {
            "mode": config.mode,
            "autotrack_camera": autotrack_cam,
            "cameras": cameras_out,
        }

    @app.post('/api/accuracy/vision/sync-cameras')
    async def sync_vision_cameras(body: dict):
        items = body.get("cameras") or []
        summary = lab.sync_cameras(items, create_missing=bool(body.get("create_missing", True)))
        lab.ensure_worker()
        return {"ok": True, **summary}

    @app.get('/api/accuracy/vision/state')
    async def get_vision_state(camera: str | None = None):
        lab.ensure_worker()
        state = lab.vision_state(camera)
        config = runtime.settings.tracking.vision
        if camera:
            cid = lab.resolve_camera_id(camera)
            keys = lab.camera_keys(cid)
            state["rois"] = [_serialize_roi(r) for r in (config.rois or []) if r.camera in keys]
        else:
            state["rois"] = [_serialize_roi(r) for r in (config.rois or [])]
        return state

    @app.post('/api/accuracy/vision/mode')
    async def set_vision_mode(body: dict):
        config = runtime.settings.tracking.vision
        mode = body.get("mode")
        camera = body.get("camera")
        cam_mode = body.get("camera_mode")
        valid = {"off", "advisory", "active"}
        if mode is not None:
            if mode not in valid:
                raise ValueError(f"Unsupported vision mode: {mode}")
            config.mode = mode
        if camera is not None and cam_mode is not None:
            if cam_mode not in valid:
                raise ValueError(f"Unsupported camera vision mode: {cam_mode}")
            cid = lab.resolve_camera_id(camera)
            if cid not in {c.id for c in runtime.settings.cameras}:
                raise ValueError("Unknown camera")
            if cam_mode != "off" and config.mode == "off":
                config.mode = cam_mode
            for old_key in lab.camera_keys(cid):
                config.camera_modes.pop(old_key, None)
            config.camera_modes[cid] = cam_mode
        runtime.save()
        lab.ensure_worker()
        return {
            "ok": True,
            "mode": config.mode,
            "camera_modes": dict(config.camera_modes),
        }

    @app.post('/api/accuracy/vision/rois')
    async def update_vision_rois(body: dict):
        from confdeck.tracking_math.config import ROI

        config = runtime.settings.tracking.vision
        action = str(body.get("action") or "set_camera").strip()
        camera = lab.resolve_camera_id(body.get("camera"))
        if camera and camera not in {c.id for c in runtime.settings.cameras}:
            raise ValueError("Unknown camera")
        cam_keys = lab.camera_keys(camera) if camera else set()
        preset = str(body.get("preset") or "").strip()

        def _clamp_box(box) -> tuple[int, int, int, int]:
            x, y, w, h = [int(round(float(v))) for v in box]
            x = max(0, min(319, x))
            y = max(0, min(179, y))
            w = max(1, min(320 - x, w))
            h = max(1, min(180 - y, h))
            return (x, y, w, h)

        def _build_roi(raw: dict) -> ROI:
            masks_raw = raw.get("masks") or []
            masks = [_clamp_box(m) for m in masks_raw[:8] if isinstance(m, (list, tuple)) and len(m) == 4]
            data = {
                "camera": lab.resolve_camera_id(raw.get("camera") or camera),
                "preset": str(raw.get("preset", preset) or ""),
                "seat": str(raw.get("seat") or "").strip(),
                "rect": _clamp_box(raw.get("rect") or [0, 0, 1, 1]),
                "masks": masks,
                "kind": str(raw.get("kind") or "seat"),
            }
            if raw.get("face_signature") is not None:
                data["face_signature"] = [float(v) for v in raw["face_signature"]]
            if raw.get("signature_threshold") is not None:
                data["signature_threshold"] = float(raw["signature_threshold"])
            return ROI(**data)

        existing = list(config.rois or [])
        if action == "clear_camera" and camera:
            existing = [
                r for r in existing
                if not (r.camera in cam_keys and (not preset or r.preset in ("", preset)))
            ]
        elif action == "remove":
            seat = str(body.get("seat") or "").strip()
            existing = [
                r for r in existing
                if not (r.seat == seat and (not cam_keys or r.camera in cam_keys))
            ]
        elif action == "add":
            new_roi = _build_roi(body.get("roi") or body)
            existing = [
                r for r in existing
                if not (
                    r.seat == new_roi.seat
                    and r.camera in lab.camera_keys(new_roi.camera)
                    and r.preset == new_roi.preset
                )
            ]
            existing.append(new_roi)
        elif action == "set_camera" and camera:
            kept = [
                r for r in existing
                if not (r.camera in cam_keys and (not preset or r.preset in ("", preset)))
            ]
            incoming = [_build_roi(item) for item in (body.get("rois") or [])]
            existing = kept + incoming
        else:
            raise ValueError(f"Unsupported ROI action: {action}")

        config.rois = existing
        runtime.save()
        lab.ensure_worker()
        return {
            "ok": True,
            "rois": [_serialize_roi(r) for r in config.rois if not cam_keys or r.camera in cam_keys],
        }

    @app.post('/api/accuracy/vision/camera-moving')
    async def notify_camera_moving(body: dict):
        camera = lab.resolve_camera_id(body.get("camera"))
        if not camera:
            return {"ok": False, "reason": "missing camera"}
        seconds = float(body.get("seconds", 1.2) or 1.2)
        until = lab.set_camera_moving(camera, seconds)
        recalled = None
        if body.get("preset") is not None or body.get("track"):
            recalled = lab.notify_preset_recalled(
                camera,
                preset=str(body.get("preset") or ""),
                seat=body.get("seat"),
            )
        return {"ok": True, "camera": camera, "moving_until": until, "post_preset": recalled}

    @app.post('/api/accuracy/vision/preset-recalled')
    async def vision_preset_recalled(body: dict):
        camera = lab.resolve_camera_id(body.get("camera"))
        if not camera:
            return {"ok": False, "reason": "missing camera"}
        seconds = float(body.get("seconds", 1.2) or 1.2)
        lock = lab.notify_preset_recalled(
            camera,
            preset=str(body.get("preset") or ""),
            seat=body.get("seat"),
            settle_seconds=seconds,
        )
        return {"ok": True, "camera": camera, "post_preset": lock}

    @app.post('/api/accuracy/vision/bind-face')
    async def bind_face_to_seat(body: dict):
        camera = lab.resolve_camera_id(body.get("camera"))
        seat = str(body.get("seat") or "").strip()
        preset = str(body.get("preset") or "").strip()
        action = str(body.get("action") or "bind").strip().lower()
        config = runtime.settings.tracking.vision
        cam_keys = lab.camera_keys(camera) if camera else set()
        roi = next(
            (
                r for r in (config.rois or [])
                if r.seat == seat and (not cam_keys or r.camera in cam_keys) and (not preset or r.preset in ("", preset))
            ),
            None,
        )
        if roi is None:
            raise ValueError(f"Draw a ROI for seat {seat!r} first")
        if action == "clear":
            roi.face_signature = None
            runtime.save()
            return {"ok": True, "seat": seat, "bound": False}

        from confdeck.vision.mouth import SeatVisionDetector
        import numpy as np

        pixels = None
        if body.get("pixels"):
            if len(body["pixels"]) > 76800:
                raise ValueError("Frame too large")
            raw = base64.b64decode(body["pixels"], validate=True)
            if len(raw) != 320 * 180:
                raise ValueError("Expected 320x180 grayscale bytes")
            pixels = np.frombuffer(raw, dtype=np.uint8).reshape(180, 320)
        elif hasattr(lab, "last_frames") and camera in lab.last_frames:
            pixels = lab.last_frames[camera]

        if pixels is None:
            raise ValueError("No camera frame available yet to sample face")

        processor = lab.vision.get(camera)
        box = None
        if body.get("box") and len(body["box"]) >= 4:
            box = [int(round(float(v))) for v in body["box"][:4]]
        elif processor is not None:
            box = getattr(processor, "detected_face_boxes", {}).get(seat)
        if box is None:
            box = list(roi.rect)

        sig = SeatVisionDetector.extract_face_signature(pixels, box)
        if not sig:
            raise ValueError("Could not extract face signature from target box")
        roi.face_signature = sig
        if body.get("signature_threshold") is not None:
            roi.signature_threshold = float(body["signature_threshold"])
        runtime.save()
        return {"ok": True, "seat": seat, "bound": True, "signature_len": len(sig), "roi": _serialize_roi(roi)}

    @app.post('/api/accuracy/vision/track')
    async def set_vision_track(body: dict):
        cid = lab.resolve_camera_id(body.get("camera"))
        if not cid:
            cam = next((c for c in runtime.settings.cameras if c.ip == "192.168.1.128"), None)
            cid = cam.id if cam else ""
        if cid not in {c.id for c in runtime.settings.cameras}:
            raise ValueError("Unknown camera")
        req_mode = str(body.get("mode") or ("off" if body.get("enabled") is False else "vision")).strip()
        enabled = bool(body.get("enabled", req_mode != "off")) and req_mode != "off"
        if enabled:
            cfg = runtime.settings.tracking.vision
            if cfg.mode == "off":
                cfg.mode = "advisory"
            if lab.effective_camera_mode(cid, cfg) == "off":
                cfg.camera_modes[cid] = cfg.mode
            runtime.save()
        lab.ensure_worker()
        payload = {
            "camera": cid,
            "mode": req_mode if enabled else "off",
            "enabled": enabled,
        }
        for route in app.routes:
            if getattr(route, "path", None) == "/api/accuracy/vision/autotrack" and "POST" in (getattr(route, "methods", None) or ()):
                return await route.endpoint(payload)
        raise ValueError("Autotrack route unavailable")

    @app.get('/api/accuracy/vision/autotrack')
    async def get_vision_autotrack():
        cam_id = lab.resolve_camera_id(getattr(lab, "autotrack_camera", None))
        cam = next((c for c in runtime.settings.cameras if c.id == cam_id or c.ip == "192.168.1.128"), None)
        native = bool(cam and getattr(cam, "native_tracking", False))
        return {
            "enabled": bool(cam_id or native),
            "camera": cam_id or (cam.id if cam else ""),
            "mode": "vision" if cam_id else ("native" if native else "off")
        }

    @app.post('/api/accuracy/vision/autotrack')
    async def set_vision_autotrack(body: dict):
        enable = bool(body.get("enabled", True))
        cid = body.get("camera", "0637f621814b")
        mode = body.get("mode", "vision")
        camera = next((c for c in runtime.settings.cameras if c.id == cid or c.ip == "192.168.1.128"), None)
        if not camera and runtime.settings.cameras:
            camera = runtime.settings.cameras[0]
        if not camera:
            raise ValueError("No camera configured")

        cid = camera.id
        if enable:
            if mode == "native":
                # Ensure vision servo driver is stopped
                if getattr(lab, "autotrack_driver", None) and getattr(lab, "autotrack_driving", False):
                    try:
                        await lab.autotrack_driver.stop()
                    except Exception:
                        pass
                lab.autotrack_camera = None
                lab.autotrack_driving = False

                import httpx, json
                async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                    await client.get(
                        f"http://{camera.ip}:{camera.control_port}/ajaxcom",
                        params={"szCmd": json.dumps({"SetEnv": {"MonoTracking": {"bEnable": 1, "nTrackMode": 0}}})},
                    )
                camera.native_tracking = True
            else:
                # Ensure camera hardware MonoTracking is disabled
                if getattr(camera, "native_tracking", False):
                    try:
                        import httpx, json
                        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                            await client.get(
                                f"http://{camera.ip}:{camera.control_port}/ajaxcom",
                                params={"szCmd": json.dumps({"SetEnv": {"MonoTracking": {"bEnable": 0, "nTrackMode": 0}}})},
                            )
                    except Exception:
                        pass
                    camera.native_tracking = False

                camera.control_protocol = "visca"
                camera.control_enabled = True
                camera.visca_port = int(getattr(camera, "visca_port", 52381) or 52381)
                from confdeck.visca import ViscaIpDriver
                lab.autotrack_driver = ViscaIpDriver(camera)
                lab.autotrack_drivers[cid] = lab.autotrack_driver
                lab.autotrack_camera = cid
                lab.autotrack_driving = False
                lab.autotrack_driving_cams[cid] = False
        else:
            if getattr(camera, "native_tracking", False):
                try:
                    import httpx, json
                    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        await client.get(
                            f"http://{camera.ip}:{camera.control_port}/ajaxcom",
                            params={"szCmd": json.dumps({"SetEnv": {"MonoTracking": {"bEnable": 0, "nTrackMode": 0}}})},
                        )
                except Exception:
                    pass
                camera.native_tracking = False
            if getattr(lab, "autotrack_driver", None):
                try:
                    await lab.autotrack_driver.stop()
                except Exception:
                    pass
            lab.autotrack_camera = None
            lab.autotrack_driving = False

        runtime.save()
        return {
            "enabled": enable,
            "camera": cid,
            "mode": mode if enable else "off"
        }
