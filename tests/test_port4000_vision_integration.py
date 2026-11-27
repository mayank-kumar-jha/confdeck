"""End-to-end local verification of Port 4000 <-> ConfDeck AI Vision integration."""

import base64
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OMNI_CANDIDATE = ROOT / "artifacts" / "mini31-candidate" / "omnitrack-sennheiser"
OMNI_BASE = ROOT / "integration" / "current84" / "omnitrack-sennheiser"


def _make_runtime(tmp_path: Path):
    from confdeck.evaluation.lab import AccuracyLab
    from confdeck.models import Camera, Settings
    from confdeck.tracking_math.config import VisionSettings

    cfg = Settings(
        cameras=[
            Camera(
                id="0637f621814b",
                name="Tracking 128",
                ip="192.168.1.128",
                port=554,
                path="/live/0",
                control_protocol="visca",
                control_enabled=True,
                visca_port=52381,
            )
        ]
    )
    cfg.tracking.vision = VisionSettings(mode="off")
    cfg.tracking.zones = []
    saved = []

    def _save():
        saved.append(cfg.model_dump())

    runtime = SimpleNamespace(
        root=tmp_path,
        settings=cfg,
        follow=SimpleNamespace(engine=SimpleNamespace(candidate=None, current=None), lab=None),
        manual=SimpleNamespace(leases=set()),
        store=SimpleNamespace(state={"current_preset": {}}, save=lambda s: saved.append(s)),
        save=_save,
    )
    runtime.follow.lab = AccuracyLab(runtime)
    return runtime, saved


def test_zero_roi_face_detection_drives_autotrack(monkeypatch, tmp_path):
    """SeatVisionDetector populates all_faces with 0 ROIs and autotrack follows them."""
    import asyncio
    from confdeck.vision.mouth import SeatVisionDetector

    # Realistic 15-element YuNet detection vector on right side [250, 40, 40, 45]
    fake_face = np.array(
        [250, 40, 40, 45, 262, 54, 278, 54, 270, 64, 263, 74, 277, 74, 0.95],
        dtype=np.float32,
    )
    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (1, [fake_face]))

    det = SeatVisionDetector()
    frame = np.zeros((180, 320), dtype=np.uint8)
    scores = det.process(frame, {}, time.monotonic())
    assert scores == {}
    assert len(det.all_faces) == 1
    assert det.all_faces[0] == [250, 40, 40, 45]
    assert det.unassigned_faces == [[250, 40, 40, 45]]

    runtime, _ = _make_runtime(tmp_path)
    runtime.settings.tracking.vision.mode = "advisory"
    lab = runtime.follow.lab
    lab.sync_cameras([{"key": "camera_2", "name": "Tracking 128", "ip": "192.168.1.128"}], create_missing=False)
    lab.autotrack_camera = "0637f621814b"
    driven = []

    class DummyDriver:
        async def drive(self, pan, tilt, zoom=0, speed=1.0):
            driven.append((pan, tilt, zoom, speed))

        async def stop(self):
            driven.append((0, 0, 0, 0))

    lab.autotrack_driver = DummyDriver()
    lab.last_autotrack_time = 0.0

    res = asyncio.run(lab.frame("camera_2", "", frame))
    assert res["all_faces"] == [[250, 40, 40, 45]]
    assert len(driven) == 1
    assert driven[0][0] > 0  # Face on right (x=270 > 160) commands positive pan


def test_empty_seat_veto_overrides_acoustic_attraction():
    """LLR_EMPTY (-4.0) suppresses positive acoustic log-likelihood even at default vision_weight."""
    from confdeck.tracking import TrackingSettings, Zone
    from confdeck.tracking_math.config import VisionSettings
    from confdeck.tracking_math.engine import SphericalFilter

    settings = TrackingSettings(
        vision=VisionSettings(mode="active"),
        zones=[
            Zone(id="s1", name="Seat 1", azimuth=0.0, elevation=10.0, radius=25.0, presets={"cam1": "1"}),
            Zone(id="s2", name="Seat 2", azimuth=28.0, elevation=10.0, radius=25.0, presets={"cam1": "2"}),
        ],
    )
    assert settings.inference.vision_weight == pytest.approx(0.15)
    flt = SphericalFilter(settings)

    # Acoustic beam aims near s1 while s1 is visually empty (-4.0) and s2 is occupied (-0.5).
    t0 = 100.0
    for i in range(6):
        flt.feed(
            {
                "azimuth": 0.0,
                "elevation": 10.0,
                "level": -38.0,
                "activity": True,
                "sample_time": t0 + i * 0.25,
                "vision_llr": {"s1": -4.0, "s2": -0.5},
            },
            now=t0 + i * 0.25,
        )
    # Empty seat s1 posterior must be suppressed below occupied seat s2.
    assert flt.posterior[0] < flt.posterior[1]


def test_port4000_flask_and_confdeck_end_to_end(monkeypatch, tmp_path):
    """Port 4000 Flask sennheiser_bp runs AI Vision cleanly against an UNALTERED ConfDeck 8765 surface."""
    monkeypatch.setenv("PORT4000_DISABLE_VISION_WORKER", "1")
    monkeypatch.setenv("OMNITRACK_DIR", str(tmp_path))

    (tmp_path / "camera_config.json").write_text(
        json.dumps(
            {
                "num_cameras": 2,
                "camera_config": {
                    "camera_1": {"cam_name": "Overview 106", "ip": "192.168.1.106", "rtsp_url": "rtsp://admin:admin@192.168.1.106:554/stream1"},
                    "camera_2": {"cam_name": "Tracking 128", "ip": "192.168.1.128", "protocol": "visca", "rtsp_url": "rtsp://admin:admin@192.168.1.128:554/live/av0"},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "camera_rtsp.json").write_text(
        json.dumps(
            {
                "camera_1": {"rtsp_url": "rtsp://admin:admin@192.168.1.106:554/stream1"},
                "camera_2": {"rtsp_url": "rtsp://admin:admin@192.168.1.128:554/live/av0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "mapping_data.json").write_text(
        json.dumps([{"serial": "sennheiser:seat_1", "person_name": "Seat 1", "preset": "1", "camera": "2"}]),
        encoding="utf-8",
    )

    from fastapi import HTTPException
    from confdeck.tracking import TrackingSettings
    runtime, _ = _make_runtime(tmp_path)

    # Mock ONLY the pre-existing ConfDeck 8765 routes (never any custom vision/state/rois/mode/track routes on 8765!)
    cd_app = FastAPI()

    @cd_app.get("/api/state")
    async def cd_state():
        return {"settings": runtime.settings.model_dump(), "tracking": {}, "microphones": []}

    @cd_app.put("/api/tracking/settings")
    async def cd_put_tracking(body: dict):
        runtime.settings.tracking = TrackingSettings(**body)
        return {"ok": True}

    @cd_app.get("/api/accuracy/vision/autotrack")
    async def cd_get_autotrack():
        return {
            "enabled": bool(runtime.follow.lab.autotrack_camera),
            "camera": runtime.follow.lab.autotrack_camera or "",
            "mode": "vision" if runtime.follow.lab.autotrack_camera else "off",
        }

    @cd_app.post("/api/accuracy/vision/autotrack")
    async def cd_post_autotrack(body: dict):
        cid = body.get("camera")
        if cid not in {c.id for c in runtime.settings.cameras}:
            raise HTTPException(400, f"Unknown camera: {cid}")
        en = bool(body.get("enabled", True))
        runtime.follow.lab.autotrack_camera = cid if en else None
        return {"enabled": en, "camera": cid, "mode": body.get("mode", "vision") if en else "off"}

    @cd_app.post("/api/accuracy/vision/frame")
    async def cd_post_frame(body: dict):
        cid = body.get("camera")
        if cid not in {c.id for c in runtime.settings.cameras}:
            raise HTTPException(400, f"Unknown camera: {cid}")
        raw = base64.b64decode(body.get("pixels", ""), validate=True)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(180, 320)
        return await runtime.follow.lab.frame(cid, body.get("preset", ""), arr, bool(body.get("quiet")))

    cd_client = TestClient(cd_app)

    # Boot Flask test app importing candidate sennheiser_routes
    for p in (
        OMNI_CANDIDATE,
        OMNI_CANDIDATE / "backend_services",
        OMNI_BASE / "backend_services",
        OMNI_BASE / "backend_services" / "services",
    ):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from flask import Flask, jsonify
    driver_sennheiser = importlib.import_module("backend_services.driver_sennheiser")
    sennheiser_routes = importlib.import_module("backend_services.routes.sennheiser_routes")
    sennheiser_routes = importlib.reload(sennheiser_routes)

    def _fake_request_confdeck(path, body=None, method=None, timeout=3):
        m = (method or ("POST" if body is not None else "GET")).upper()
        url = f"/api/{path}"
        headers = {"X-ConfDeck": "1"}
        if m == "GET":
            resp = cd_client.get(url, headers=headers)
        elif m == "DELETE":
            resp = cd_client.request("DELETE", url, json=body, headers=headers)
        else:
            resp = cd_client.request(m, url, json=body, headers=headers)
        data = resp.json() if resp.content else {}
        return data, resp.status_code

    monkeypatch.setattr(driver_sennheiser, "request_confdeck", _fake_request_confdeck)
    monkeypatch.setattr(sennheiser_routes, "request_confdeck", _fake_request_confdeck)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(sennheiser_routes.sennheiser_bp)

    @flask_app.route("/api/ptz/preset/recall", methods=["POST"])
    def _dummy_ptz_recall():
        return jsonify({"ok": True})

    client = flask_app.test_client()

    # 1. Camera listing resolves camera_2 -> 0637f621814b and preserves preview-only camera_1
    r = client.get("/api/sennheiser/vision/cameras")
    assert r.status_code == 200
    cams = r.get_json()
    keys = {c["key"]: c for c in cams["cameras"]}
    assert "camera_1" in keys and keys["camera_1"]["confdeck_registered"] is False
    assert keys["camera_2"]["id"] == "0637f621814b"
    assert keys["camera_2"]["confdeck_registered"] is True
    assert keys["camera_2"]["mjpeg_url"] == "/api/ptz/preview_feed?camera=camera_2"
    assert len(cams["seats"]) == 1 and cams["seats"][0]["id"] == "Seat 1"

    # 2. Enabling tracking via Port 4000 auto-enables advisory vision + auto-track ROI & sets autotrack on 8765
    r = client.post(
        "/api/sennheiser/accuracy/vision/track",
        json={"camera": "camera_2", "mode": "vision", "enabled": True},
    )
    assert r.status_code == 200
    assert runtime.settings.tracking.vision.mode == "advisory"
    assert runtime.follow.lab.autotrack_camera == "0637f621814b"
    assert any(roi.camera == "0637f621814b" for roi in runtime.settings.tracking.vision.rois)

    # 3. Switching global mode to active and saving ROIs via alias camera_2 persists via PUT /api/tracking/settings
    r = client.post("/api/sennheiser/accuracy/vision/mode", json={"mode": "active"})
    assert r.status_code == 200 and r.get_json()["mode"] == "active"

    r = client.post(
        "/api/sennheiser/accuracy/vision/rois",
        json={
            "action": "set_camera",
            "camera": "camera_2",
            "preset": "",
            "rois": [{"seat": "Seat 1", "rect": [40, 20, 80, 90], "masks": [[200, 10, 60, 40]]}],
        },
    )
    assert r.status_code == 200
    assert r.get_json()["rois"][0]["camera"] == "0637f621814b"

    # 4. Feeding 320x180 grayscale frame via Port 4000 populates /vision/state on Port 4000
    b64 = base64.b64encode(np.zeros((180, 320), dtype=np.uint8).tobytes()).decode("ascii")
    r = client.post(
        "/api/sennheiser/accuracy/vision/frame",
        json={"camera": "camera_2", "preset": "", "pixels": b64},
    )
    assert r.status_code == 200
    st = client.get("/api/sennheiser/accuracy/vision/state?camera=camera_2")
    assert st.status_code == 200
    st_json = st.get_json()
    assert st_json["camera"] == "0637f621814b"
    assert st_json["alias"] == "camera_2"
    assert len(st_json["rois"]) == 1

    # 5. Port 4000 PTZ preset recall sets moving guard inside Port 4000 immediately
    before = time.monotonic()
    r = client.post("/api/ptz/preset/recall", json={"camera": "camera_2", "preset": "1"})
    assert r.status_code == 200
    assert sennheiser_routes._MOVING_UNTIL.get("0637f621814b", 0.0) > before

    # 6. Verify setup.html & vision_module.js wiring
    setup_html = (OMNI_CANDIDATE / "public" / "html" / "setup.html").read_text(encoding="utf-8")
    assert 'id="nav-vision"' in setup_html
    assert 'id="page-vision"' in setup_html
    assert "js/modules/vision_module.js" in setup_html
    assert (OMNI_CANDIDATE / "public" / "js" / "modules" / "vision_module.js").is_file()
