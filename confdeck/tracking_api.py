"""Local-admin endpoints. The paired display listener never installs these routes."""

import asyncio
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from confdeck.follow import validate_recording
from confdeck.microphone import tracking_scenario
from confdeck.tracking import PROFILES, TrackingSettings, distance


class CaptureScene(BaseModel):
    camera: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    confirm_update: bool = False
    existing_token: str | None = Field(default=None, min_length=1, max_length=128)


class Mode(BaseModel):
    live: bool = False


class Changes(BaseModel):
    changes: dict = Field(default_factory=dict)
    confirm: bool = False


class Recall(BaseModel):
    camera: str = Field(max_length=40)
    preset: str = Field(max_length=128)
    confirm: bool = False


class Recording(BaseModel):
    action: Literal["start", "stop"]


class Replay(BaseModel):
    events: list[dict] = Field(max_length=10000)
    speed: float = Field(default=1, ge=.25, le=8)


def add_tracking_routes(app, runtime):
    from confdeck.handoff_api import add_handoff_routes
    add_handoff_routes(app, runtime)
    from confdeck.models import Scene
    from confdeck.tuning_preview import preview
    preview_lock = asyncio.Lock()

    @app.post("/api/tracking/preview")
    async def preview_settings(body: TrackingSettings):
        async with preview_lock:
            now = time.monotonic()
            rows = [dict(r, event=dict(r["event"])) for r in runtime.follow.preview_events if now-r["t"] <= 60]
            return await asyncio.to_thread(preview, rows, body)


    @app.post("/api/scenes/capture")
    async def capture_scene(body: CaptureScene):
        from confdeck.ptz import CameraControl
        name = body.name.strip()
        if not name:
            raise ValueError("Enter a scene name.")
        async with runtime.write_lock:
            camera = next((c for c in runtime.settings.cameras if c.id == body.camera), None)
            if not camera or not camera.control_enabled or camera.control_protocol == "none" or camera.native_tracking or camera.demo:
                raise ValueError("Select a camera with preset control enabled.")
            if runtime.follow.handoffs.busy(camera.id):
                raise ValueError("Pause tracking handoff before capturing a scene.")
            if camera.id in runtime.manual.leases:
                raise ValueError("Release the joystick and wait for the camera to stop before saving.")
            matches = [s for s in runtime.settings.scenes if s.name.strip().casefold() == name.casefold()]
            if matches and not body.confirm_update:
                raise ValueError("This scene exists. Confirm updating its saved framing.")
            if not matches and len(runtime.settings.scenes) >= 64:
                raise ValueError("The scene limit has been reached.")
            ids = {s.id for s in matches}
            mapping = {cid: slot for s in matches for cid, slot in s.presets.items()}
            token = mapping.get(camera.id)
            used_elsewhere = {s.presets[camera.id] for s in runtime.settings.scenes
                              if s.id not in ids and camera.id in s.presets}
            used_elsewhere.update(z.presets[camera.id] for z in runtime.settings.tracking.zones
                                  if z.scene_id not in ids and camera.id in z.presets)
            if camera.id in runtime.settings.tracking.wide_presets:
                used_elsewhere.add(runtime.settings.tracking.wide_presets[camera.id])
            reserved = [str(n) for n in range(camera.preset_start, camera.preset_end + 1) if n not in camera.protected_presets]
            lock = runtime.preset_locks.setdefault(camera.id, asyncio.Lock())
            if lock.locked():
                raise ValueError("Wait for the current camera command to finish.")
            runtime.follow.stop("Saving joystick framing")
            async with lock:
                async with CameraControl(camera, runtime.secrets.get(camera.id)) as control:
                    records = dict(camera.preset_records)
                    if camera.control_protocol != "visca":
                        catalog = await control.list()
                        records = {p["token"]: p["name"] for p in catalog["presets"]}
                        if camera.control_protocol == "canon":
                            reserved = [n for n in reserved if 1 <= int(n) <= catalog["limit"]]
                    if body.existing_token is not None:
                        token = body.existing_token
                        if token not in records:
                            raise ValueError("This preset is no longer available on the camera.")
                    else:
                        if camera.control_protocol == "onvif":
                            if token not in records or token in used_elsewhere or token in {str(n) for n in camera.protected_presets}:
                                token = ""
                        elif token not in reserved or token not in records or token in used_elsewhere:
                            token = next((n for n in reserved if n not in records and n not in used_elsewhere), None)
                            if token is None:
                                raise ValueError("No free camera memories remain in the reserved range.")
                        result = await control.execute("save", token, name)
                        if camera.control_protocol == "onvif":
                            token = result.get("token") or token
                        if not token:
                            raise ValueError("The camera did not return a saved preset token.")
                        records[token] = name
                    camera.preset_records = records
                mapping[camera.id] = token
                scene = Scene(id=matches[0].id if matches else uuid.uuid4().hex, name=name, presets=mapping)
                runtime.settings.scenes = [s for s in runtime.settings.scenes if s.id not in ids] + [scene]
                for zone in runtime.settings.tracking.zones:
                    if zone.scene_id in ids:
                        zone.scene_id = scene.id
                        zone.presets = dict(mapping)
                runtime.follow.configure(runtime.settings.tracking)
                runtime.save()
            return {"ok": True, "scene": scene.model_dump()}

    @app.get("/api/scenes")
    async def scenes():
        return [s.model_dump() for s in runtime.settings.scenes]

    @app.put("/api/scenes")
    async def save_scenes(body: list[Scene]):
        if len(body) > 64 or len({s.id for s in body}) != len(body):
            raise ValueError("Use at most 64 scenes with unique IDs.")
        for scene in body:
            for cid, token in scene.presets.items():
                runtime.follow.camera(cid, token)
        async with runtime.write_lock:
            linked = {z.scene_id for z in runtime.settings.tracking.zones if z.scene_id}
            if linked - {s.id for s in body}:
                raise ValueError("Unlink seats before removing their scenes.")
            for zone in runtime.settings.tracking.zones:
                if zone.scene_id:
                    zone.presets = dict(next(s for s in body if s.id == zone.scene_id).presets)
            runtime.follow.configure(runtime.settings.tracking)
            runtime.settings.scenes = body
            runtime.save()
        return {"ok": True}

    @app.post("/api/scenes/{scene_id}/recall")
    async def recall_scene(scene_id: str):
        scene = next((s for s in runtime.settings.scenes if s.id == scene_id), None)
        if scene is None:
            raise ValueError("Scene no longer exists.")
        async def one(cid, token):
            try:
                if runtime.follow.handoffs.busy(cid):
                    raise ValueError("Pause tracking handoff before recalling a scene.")
                camera = runtime.follow.camera(cid, token)
                if camera.control_protocol == "visca" and token not in camera.preset_records:
                    raise ValueError("Save this scene from the joystick panel before recalling it.")
                return cid, await runtime.follow.recall(cid, token)
            except (ValueError, OSError) as exc:
                return cid, {"ok": False, "error": str(exc)}
        return dict(await asyncio.gather(*(one(c, p) for c, p in scene.presets.items())))

    @app.delete("/api/scenes/{scene_id}")
    async def remove_scene(scene_id: str, confirm_unlink: bool = False):
        async with runtime.write_lock:
            if not any(s.id == scene_id for s in runtime.settings.scenes):
                raise ValueError("Scene no longer exists.")
            linked = [z for z in runtime.settings.tracking.zones if z.scene_id == scene_id]
            if linked and not confirm_unlink:
                raise ValueError("Confirm removal and unlinking from assigned seats.")
            runtime.follow.stop("Scene removed")
            for zone in linked:
                zone.scene_id = ""
                zone.presets = {}
            runtime.settings.scenes = [s for s in runtime.settings.scenes if s.id != scene_id]
            runtime.follow.configure(runtime.settings.tracking)
            runtime.save()
        return {"ok": True, "unlinked_seats": [z.name for z in linked]}

    @app.get("/api/tracking")
    async def status():
        return {"settings": runtime.settings.tracking.model_dump(), "profiles": PROFILES,
                "state": runtime.follow.snapshot()}

    @app.post("/api/tracking/conversation")
    async def conversation_settings(body: dict):
        allowed = {"conversation_layout", "presenter_seat", "presenter_camera"}
        if set(body) - allowed:
            raise ValueError("Unknown smart preview setting")
        proposed = TrackingSettings.model_validate({**runtime.settings.tracking.model_dump(), **body})
        await settings(proposed)
        return {"ok": True, "settings": runtime.settings.tracking.model_dump()}

    @app.put("/api/tracking/settings")
    async def settings(body: TrackingSettings):
        if runtime.follow.live and body.zones != runtime.settings.tracking.zones:
            raise ValueError("Switch to dry-run before editing seats.")
        for zone in body.zones:
            if not {zone.mic_id, *zone.microphone_profiles} <= {"primary", *(m.id for m in runtime.settings.microphones)}:
                raise ValueError("A seat references a microphone that is not configured.")
            previous = next((z for z in runtime.settings.tracking.zones if z.id == zone.id), None)
            if previous and (previous.azimuth, previous.elevation, previous.radius) != (zone.azimuth, zone.elevation, zone.radius):
                zone.components = []  # Manual geometry edits invalidate the old calibrated fit.
            if previous:
                for source, profile in zone.microphone_profiles.items():
                    old = previous.microphone_profiles.get(source)
                    if old and (old.azimuth, old.elevation, old.radius) != (profile.azimuth, profile.elevation, profile.radius):
                        profile.components = []
            if zone.scene_id:
                scene = next((s for s in runtime.settings.scenes if s.id == zone.scene_id), None)
                if scene is None:
                    raise ValueError("Choose an existing scene for this seat.")
                zone.presets = dict(scene.presets)
        ids = {c.id for c in runtime.settings.cameras}
        # A paired OmniTrack installation owns its cameras and their preset control.
        import os
        external_config = os.environ.get('CONFDECK_OMNITRACK_CAMERA_CONFIG')
        if external_config:
            from confdeck.omnitrack_cameras import camera_catalog
            existing = [camera.model_dump() for camera in runtime.settings.cameras]
            ids.update(c['id'] for c in camera_catalog(existing, external_config))
        if body.presenter_seat:
            if not body.presenter_camera:
                raise ValueError("Choose both the presenter seat and camera, or leave both unset.")
            presenter = next((z for z in body.zones if z.id == body.presenter_seat), None)
            if not presenter or body.presenter_camera not in presenter.presets:
                raise ValueError("Save a presenter framing on the chosen camera and link it to the presenter seat first.")
        warnings = []
        old_zones = {z.id: z.presets for z in runtime.settings.tracking.zones}
        mappings = [(body.wide_presets, runtime.settings.tracking.wide_presets)]
        mappings.extend((z.presets, old_zones.get(z.id, {})) for z in body.zones)
        for mapping, previous in mappings:
            for cid in mapping.keys() - ids:
                if previous.get(cid) != mapping[cid]:
                    raise ValueError("A zone references a camera that is no longer configured.")
                del mapping[cid]
                warnings.append("Cleared an assignment to a removed camera.")
        metric = distance
        if body.engine == "new":
            from confdeck.tracking_math.geometry import distance as spherical_distance
            from confdeck.tracking_math.geometry import vector
            def metric(a, b):
                return spherical_distance(vector(*a, body.inference.convention), vector(*b, body.inference.convention))
        for i, a in enumerate(body.zones):
            for b in body.zones[i + 1:]:
                aa = {a.mic_id: a, **a.microphone_profiles}
                bb = {b.mic_id: b, **b.microphone_profiles}
                for source in aa.keys() & bb.keys():
                    left, right = aa[source], bb[source]
                    if metric((left.azimuth, left.elevation), (right.azimuth, right.elevation)) < left.radius + right.radius:
                        warnings.append(f"{a.name} overlaps {b.name} on {source}. Test seat separation.")
        async with runtime.write_lock:
            runtime.follow.configure(body)
            runtime.settings.tracking = body
            runtime.save()
        return {"ok": True, "warnings": warnings}

    @app.post("/api/tracking/mode")
    async def mode(body: Mode):
        runtime.follow.mode(body.live)
        return runtime.follow.snapshot()

    @app.post("/api/tracking/stop")
    async def stop():
        runtime.follow.stop()
        return {"ok": True}

    @app.post("/api/tracking/learn")
    async def learn(source: str = "primary"):
        if source not in {"primary", *(m.id for m in runtime.settings.microphones)}:
            raise ValueError("Select a configured microphone.")
        return await runtime.follow.learn_zone(source)

    @app.post("/api/tracking/test-recall")
    async def recall(body: Recall):
        if not body.confirm:
            raise ValueError("Confirm this test will move the selected camera.")
        runtime.follow.stop("Manual test recall")
        result = await runtime.follow.recall(body.camera, body.preset)
        runtime.follow.actual_moves.append(time.monotonic())
        runtime.follow.audit(f"Manual test: {body.camera} preset {body.preset} accepted")
        return result

    @app.post("/api/tracking/record")
    async def record(body: Recording):
        if body.action == "start":
            runtime.follow.start_record()
        else:
            runtime.follow.recording = False
            runtime.follow.audit("Recording stopped")
        return {"count": len(runtime.follow.record)}

    @app.get("/api/tracking/recording")
    async def recording():
        return {"version": 1, "events": runtime.follow.record}

    @app.get("/api/tracking/sample")
    async def sample(scenario: Literal["jitter", "flapping", "noise"] = "jitter"):
        zones = runtime.settings.tracking.zones
        centers = [(z.azimuth, z.elevation) for z in zones[:2]] if len(zones) >= 2 else None
        return {"version": 1, "events": tracking_scenario(scenario, centers)}

    @app.post("/api/tracking/replay")
    async def replay(body: Replay):
        rows = validate_recording(body.events)
        if runtime.follow.replay_task and not runtime.follow.replay_task.done():
            raise ValueError("Stop the current replay before starting another.")
        runtime.follow.replay_state = None
        runtime.follow.replay_task = runtime.background(runtime.follow.replay(rows, body.speed))
        return {"ok": True}

    @app.post("/api/tracking/replay/stop")
    async def stop_replay():
        task = runtime.follow.replay_task
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        runtime.follow.audit("Replay stopped")
        return {"ok": True}

    async def native_request(camera, command):
        import httpx
        import json
        async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
            response = await client.get(f"http://{camera.ip}:{camera.control_port}/ajaxcom", params={"szCmd": json.dumps(command)})
            response.raise_for_status()
            data = response.json()
        if data.get("nRetVal") != 0:
            raise ValueError("Camera did not accept the tracking request.")
        return data.get("stValue", {})

    @app.get("/api/cameras/{identity}/native-tracking")
    async def native_status(identity: str):
        camera = next((c for c in runtime.settings.cameras if c.id == identity), None)
        if not camera:
            raise ValueError("Camera no longer exists.")
        if camera.control_protocol == "canon":
            raise ValueError("Manage Canon auto tracking in the camera’s Auto Tracking application.")
        value = await native_request(camera, {"GetEnv": {"MonoTracking": {"nChannel": -1}}})
        if "bEnable" not in value or "nTrackMode" not in value:
            raise ValueError("This camera does not expose compatible monocular tracking.")
        return {"enabled": bool(value["bEnable"]), "mode": "presenter" if value["nTrackMode"] == 0 else "zone"}

    @app.post("/api/cameras/{identity}/native-tracking")
    async def native_set(identity: str, body: dict):
        if type(body.get("enabled")) is not bool or body.get("mode") not in ("presenter", "zone"):
            raise ValueError("Choose tracking on/off and presenter/zone mode.")
        camera = next((c for c in runtime.settings.cameras if c.id == identity), None)
        if not camera:
            raise ValueError("Camera no longer exists.")
        if identity in runtime.manual.leases:
            raise ValueError("Release the joystick before changing tracking.")
        if runtime.follow.handoffs.busy(identity) or camera.handoff_mode == "live":
            raise ValueError("Pause tracking handoff before changing camera-native tracking.")
        await native_status(identity)
        runtime.follow.stop("Camera-native tracking mode changed")
        # Fail closed if a write succeeds but its response/readback is lost.
        camera.native_tracking = True
        camera.control_enabled = False
        runtime.save()
        await native_request(camera, {"SetEnv": {"MonoTracking": {"bEnable": int(body["enabled"]), "nTrackMode": 0 if body["mode"] == "presenter" else 1}}})
        result = await native_status(identity)
        if result != {"enabled": body["enabled"], "mode": body["mode"]}:
            raise ValueError("Tracking readback differs; preset control stays disabled until tracking is verified off.")
        camera.native_tracking = result["enabled"]
        camera.control_enabled = False
        if not result["enabled"]:
            from confdeck.visca import ViscaIpDriver
            candidate = camera.model_copy(update={"control_protocol": "visca"})
            try:
                await ViscaIpDriver(candidate).check()
            except (ValueError, OSError, TimeoutError):
                runtime.save()
                raise ValueError("Native tracking is off, but manual control did not respond. Check the VISCA port.")
            camera.control_protocol = "visca"
            camera.visca_local_port = candidate.visca_local_port
            camera.control_enabled = True
        runtime.save()
        return result

    @app.get("/api/mic/device-settings")
    async def read_device(source: str = "primary"):
        async with runtime.mic_lock:
            return await device_service(source).read()

    def device_service(source):
        if source == "primary":
            return runtime.device_settings
        if source not in {m.id for m in runtime.settings.microphones}:
            raise ValueError("Select a configured microphone.")
        from confdeck.device_settings import DeviceSettings
        return DeviceSettings(runtime, source)

    @app.post("/api/mic/device-settings")
    async def write_device(body: Changes, source: str = "primary"):
        runtime.follow.stop("Microphone settings changed")
        async with runtime.mic_lock:
            return await device_service(source).apply(body.changes, body.confirm)

    @app.post("/api/mic/device-settings/revert")
    async def revert_device(body: Changes, source: str = "primary"):
        runtime.follow.stop("Microphone snapshot restore")
        async with runtime.mic_lock:
            return await device_service(source).apply({}, body.confirm, revert=True)
