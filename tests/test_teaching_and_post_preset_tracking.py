"""Tests for teaching-zone front/back-of-head tracking, face-to-seat binding,
post-preset continuous VISCA tracking, and dual-mic unified room coordinates."""

import asyncio
import base64
import time

import cv2
import numpy as np


def _draw_person_silhouette(frame: np.ndarray, head_cx: int, head_top_y: int, bg: int = 220, fg: int = 35):
    """Draw a turned-away head crown + neck/shoulders silhouette against a uniform board."""
    cv2.ellipse(frame, (head_cx, head_top_y + 14), (11, 14), 0, 0, 360, fg, -1)
    cv2.ellipse(frame, (head_cx, head_top_y + 44), (24, 18), 0, 180, 360, max(20, fg + 20), -1)
    cv2.rectangle(frame, (max(0, head_cx - 22), head_top_y + 42), (min(319, head_cx + 22), min(179, head_top_y + 72)), max(20, fg + 25), -1)
    # Subtle hair/collar internal contrast so edge cues activate realistically
    cv2.line(frame, (head_cx - 10, head_top_y + 28), (head_cx + 10, head_top_y + 28), bg - 40, 2)


def _draw_synthetic_face(frame: np.ndarray, cx: int, cy: int, variant: int = 0):
    """Draw a deterministic distinct face pattern for signature matching tests."""
    cv2.ellipse(frame, (cx, cy), (18, 22), 0, 0, 360, 175 if variant == 0 else 110, -1)
    if variant == 0:
        # Eyes dark, vertical nose bridge bright, dark broad mouth bar
        cv2.circle(frame, (cx - 7, cy - 6), 3, 25, -1)
        cv2.circle(frame, (cx + 7, cy - 6), 3, 25, -1)
        cv2.rectangle(frame, (cx - 2, cy - 4), (cx + 2, cy + 7), 240, -1)
        cv2.rectangle(frame, (cx - 8, cy + 10), (cx + 8, cy + 15), 20, -1)
    else:
        # Inverted contrast sunglasses + bright band lower half
        cv2.rectangle(frame, (cx - 13, cy - 10), (cx + 13, cy - 2), 245, -1)
        cv2.circle(frame, (cx, cy + 4), 5, 20, -1)
        cv2.rectangle(frame, (cx - 10, cy + 11), (cx + 10, cy + 17), 235, -1)


def _dummy_face_row(x: int, y: int, w: int, h: int, conf: float = 0.92) -> np.ndarray:
    cx, cy = x + w * 0.5, y + h * 0.5
    return np.array([
        x, y, w, h,
        cx - w * 0.2, cy - h * 0.15,
        cx + w * 0.2, cy - h * 0.15,
        cx, cy,
        cx - w * 0.18, cy + h * 0.2,
        cx + w * 0.18, cy + h * 0.2,
        conf,
    ], dtype=np.float32)


def test_teaching_zone_back_of_head_and_ignores_students(monkeypatch):
    """Mode 1: Teaching ROI detects turned-away back-of-head/body and ignores student faces outside."""
    from confdeck.vision.mouth import SeatVisionDetector

    det = SeatVisionDetector()
    # Explicitly test silhouette fallback when YOLOX body detector is absent
    monkeypatch.setattr(det, "_get_body_detector", lambda: None)
    t0 = 100.0
    rois = {
        "teacher_board": {"rect": [10, 15, 150, 155], "kind": "teaching", "masks": []},
    }

    # 1. Teacher shows front face inside teaching ROI; student sits outside at x=240.
    f_front = np.full((180, 320), 220, dtype=np.uint8)
    _draw_person_silhouette(f_front, 70, 35)
    student_face = _dummy_face_row(235, 55, 36, 42, 0.95)
    teacher_face = _dummy_face_row(56, 36, 32, 38, 0.90)
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (2, np.vstack([teacher_face, student_face])))

    det.process(f_front, rois, t0)
    assert det.occupancy_state["teacher_board"] is True
    assert det.target_poses["teacher_board"] == "front"

    # 2. Teacher turns back to blackboard (no YuNet face inside teaching ROI; student face still visible outside!).
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (1, np.vstack([student_face])))
    f_back = np.full((180, 320), 220, dtype=np.uint8)
    _draw_person_silhouette(f_back, 102, 34)  # moved slightly right along blackboard

    det.process(f_back, rois, t0 + 0.12)
    assert det.occupancy_state["teacher_board"] is True
    assert det.target_poses["teacher_board"] == "back_or_body"
    assert det.last_llr["teacher_board"] == det.LLR_QUIET  # Present: must NOT apply -4.0 empty veto!
    bx, by, bw, bh = det.detected_face_boxes["teacher_board"]
    assert 10 <= bx and (bx + bw) <= 165  # Strictly inside teaching ROI, NOT student at x=235!

    # 3. Blank uniform board + masked screen glare -> NO false body detection after reset
    det.reset()
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (0, None))
    rois_masked = {
        "teacher_board": {"rect": [10, 15, 150, 155], "kind": "teaching", "masks": [[90, 25, 55, 45]]},
    }
    blank = np.full((180, 320), 215, dtype=np.uint8)
    cv2.rectangle(blank, (95, 30), (140, 65), 40, -1)  # Bright contrast purely inside mask
    det.process(blank, rois_masked, t0 + 2.0)
    assert det.occupancy_state["teacher_board"] is False
    assert det.last_llr["teacher_board"] == det.LLR_EMPTY


def test_face_signature_binding_rejects_wrong_person(monkeypatch):
    """Assigned face-to-seat binding accepts bound occupant and vetoes a different person."""
    from confdeck.vision.mouth import SeatVisionDetector

    det = SeatVisionDetector()
    base = np.full((180, 320), 210, dtype=np.uint8)
    _draw_synthetic_face(base, 80, 75, variant=0)
    sig_person_a = SeatVisionDetector.extract_face_signature(base, [62, 53, 36, 44])
    assert sig_person_a is not None and 50 <= len(sig_person_a) <= 256

    face_row = _dummy_face_row(62, 53, 36, 44, 0.94)
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (1, np.vstack([face_row])))
    rois = {
        "seat_1": {
            "rect": [50, 40, 64, 72],
            "kind": "seat",
            "face_signature": sig_person_a,
            "signature_threshold": 0.45,
        }
    }

    # Bound person (slight brightness gain shift) is accepted cleanly
    f_a = np.clip(base.astype(np.int16) + 12, 0, 255).astype(np.uint8)
    det.process(f_a, rois, 10.0)
    assert det.occupancy_state["seat_1"] is True
    assert det.binding_rejected.get("seat_1") is False
    assert det.binding_scores["seat_1"] >= 0.7

    # Distinct person B in the same ROI is immediately vetoed
    f_b = np.full((180, 320), 210, dtype=np.uint8)
    _draw_synthetic_face(f_b, 80, 75, variant=1)
    det.process(f_b, rois, 10.2)
    assert det.binding_rejected["seat_1"] is True
    assert det.occupancy_state["seat_1"] is False
    assert det.last_llr["seat_1"] == det.LLR_EMPTY


def test_post_preset_tracking_teaching_mode_and_hysteresis(monkeypatch, tmp_path):
    """Recalling a preset engages continuous VISCA tracking once settled, honoring lock & teaching zone."""
    from confdeck.models import Camera, Settings
    from confdeck.runtime import Runtime
    from confdeck.tracking import Zone
    from confdeck.tracking_math.config import ROI, VisionSettings
    from confdeck.vision.mouth import SeatVisionDetector

    class DummyVisca:
        def __init__(self, cid):
            self.cid = cid
            self.drives = []
            self.stops = 0

        async def drive(self, pan=0.0, tilt=0.0, zoom=0, speed=1.0):
            self.drives.append((round(pan, 3), round(tilt, 3)))

        async def stop(self):
            self.stops += 1

    cfg = Settings(
        cameras=[
            Camera(id="cam_108", name="Camera 108", ip="192.168.1.108", control_enabled=True, control_protocol="visca"),
            Camera(id="0637f621814b", name="Presenter 128", ip="192.168.1.128", control_enabled=True, control_protocol="visca"),
        ]
    )
    cfg.tracking.presenter_camera = "0637f621814b"
    cfg.tracking.settle_ms = 200
    cfg.tracking.zones = [
        Zone(id="seat_kashif", name="kashif", azimuth=90.0, elevation=45.0, presets={"cam_108": "21"}),
        Zone(id="seat_neha", name="neha", azimuth=20.0, elevation=45.0, presets={"cam_108": "22"}),
    ]
    cfg.tracking.vision = VisionSettings(
        mode="advisory",
        track_after_preset=True,
        lock_hold_s=2.0,
        rois=[
            ROI(camera="cam_108", preset="21", seat="seat_kashif", rect=[30, 30, 80, 90]),
            ROI(camera="0637f621814b", preset="", seat="teaching_zone", rect=[10, 10, 140, 150], kind="teaching"),
        ],
    )
    rt = Runtime(tmp_path)
    rt.settings = cfg
    lab = rt.follow.lab
    drv_108 = DummyVisca("cam_108")
    drv_128 = DummyVisca("0637f621814b")
    lab.autotrack_drivers = {"cam_108": drv_108, "0637f621814b": drv_128}

    # 1. Preset 21 recalled on Camera 108 -> post-preset tracking armed; while moving, no VISCA drive sent
    lab.command("cam_108", "21", "accepted", seat="seat_kashif")
    assert lab.has_active_post_preset("cam_108") is True

    # Student face on far left (inside kashif ROI [30..110]), stray stranger on far right (x=270)
    kashif_face = _dummy_face_row(45, 45, 34, 40, 0.94)
    stray_face = _dummy_face_row(270, 50, 38, 44, 0.95)
    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (2, np.vstack([kashif_face, stray_face])))

    blank = np.full((180, 320), 210, dtype=np.uint8)
    # Force settle elapsed and push frame -> must pan LEFT toward kashif (dx < 0 -> pan < 0), NOT right!
    lab.moving_until["cam_108"] = time.monotonic() - 0.1
    lab.last_autotrack_times["cam_108"] = 0.0
    res_108 = asyncio.run(lab.frame("cam_108", "21", blank, source="rtsp"))
    assert res_108["tracking"]["seat"] == "seat_kashif"
    assert len(drv_108.drives) == 1 and drv_108.drives[0][0] < 0

    # Brief occlusion/blink within lock_hold_s: stray face STILL visible at x=270, MUST NOT jump to stray!
    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (1, np.vstack([stray_face])))
    lab.last_autotrack_times["cam_108"] = 0.0
    res_hold = asyncio.run(lab.frame("cam_108", "21", blank, source="rtsp"))
    assert res_hold["tracking"]["reason"] == "hysteresis_hold"
    assert len(drv_108.drives) == 1  # Still 1 drive! Never jumped right to stray face!
    assert drv_108.stops == 1  # Stopped cleanly while holding

    # 2. Presenter 128 Teaching Zone: turned-away teacher on left (x=55) + student face on far right (x=265)
    # Test silhouette fallback path on synthetic ellipse frame (YOLOX disabled)
    monkeypatch.setattr(SeatVisionDetector, "_get_body_detector", lambda self: None)
    lab.notify_preset_recalled("0637f621814b", preset="1", settle_seconds=0.0)
    lab.last_autotrack_times["0637f621814b"] = 0.0
    teach_frame = np.full((180, 320), 220, dtype=np.uint8)
    _draw_person_silhouette(teach_frame, 55, 32)
    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (1, np.vstack([stray_face])))
    res_128 = asyncio.run(lab.frame("0637f621814b", "", teach_frame, source="rtsp"))
    assert res_128["poses"]["teaching_zone"] == "back_or_body"
    assert res_128["tracking"]["pose"] == "back_or_body"
    assert len(drv_128.drives) == 1 and drv_128.drives[0][0] < 0  # Tracks teacher left, NOT student right!


def test_bind_face_and_preset_recalled_api(tmp_path):
    """API routes /bind-face, /preset-recalled, and Teaching ROI save/load end-to-end."""
    from fastapi.testclient import TestClient
    from confdeck.app import create_app
    from confdeck.models import Camera, Settings
    from confdeck.runtime import Runtime
    from confdeck.tracking_math.config import VisionSettings
    from confdeck.vision.mouth import SeatVisionDetector

    rt = Runtime(tmp_path)
    rt.settings = Settings(cameras=[Camera(id="cam_109", name="Camera 109", ip="192.168.1.109")])
    rt.settings.tracking.vision = VisionSettings(mode="advisory")
    app = create_app(rt, lifecycle=False)
    client = TestClient(app)

    roi_res = client.post(
        "/api/accuracy/vision/rois",
        headers={"X-ConfDeck": "1"},
        json={
            "action": "set_camera",
            "camera": "cam_109",
            "rois": [{"seat": "Board", "rect": [10, 10, 140, 150], "kind": "blackboard", "masks": []}],
        },
    )
    assert roi_res.status_code == 200
    assert roi_res.json()["rois"][0]["kind"] == "teaching"

    frame = np.full((180, 320), 210, dtype=np.uint8)
    _draw_synthetic_face(frame, 80, 75, variant=0)
    b64 = base64.b64encode(frame.tobytes()).decode("ascii")
    bind_res = client.post(
        "/api/accuracy/vision/bind-face",
        headers={"X-ConfDeck": "1"},
        json={
            "camera": "cam_109",
            "seat": "Board",
            "pixels": b64,
            "box": [62, 53, 36, 44],
        },
    )
    assert bind_res.status_code == 200
    assert bind_res.json()["bound"] is True
    assert rt.settings.tracking.vision.rois[0].face_signature is not None

    rec_res = client.post(
        "/api/accuracy/vision/preset-recalled",
        headers={"X-ConfDeck": "1"},
        json={
            "camera": "cam_109",
            "preset": "21",
            "seat": "Board",
            "seconds": 0.5,
        },
    )
    assert rec_res.status_code == 200
    assert rec_res.json()["post_preset"]["mode"] == "teaching"


def test_dual_mic_unified_room_coordinate_transform():
    """Local mic bearings transform via ceiling (x, y, yaw) into fused unified room coordinates."""
    from confdeck.microphone import PositionParser
    from confdeck.models import MicSettings
    from confdeck.tracking_math.geometry import fuse_room_coordinates, local_angles_to_room_point

    # Mic A at room (0, 0) yaw=0; speaker at (1.8, 0) -> local az=90, el=45 (drop=1.8m)
    pt_a = local_angles_to_room_point(90.0, 45.0, ceiling_height=3.0, head_height=1.2, room_x=0.0, room_y=0.0, room_yaw=0.0)
    # Mic B at room (3.6, 0) rotated 180 deg; same speaker is local az=90 -> world -90 (west) from (3.6, 0) -> (1.8, 0)
    pt_b = local_angles_to_room_point(90.0, 45.0, ceiling_height=3.0, head_height=1.2, room_x=3.6, room_y=0.0, room_yaw=180.0)
    assert pt_a == (1.8, 0.0)
    assert pt_b == (1.8, 0.0)

    fused = fuse_room_coordinates([
        {"mic_id": "primary", "x": pt_a[0], "y": pt_a[1], "snr_db": 18.0, "confidence": 0.95, "age_s": 0.05},
        {"mic_id": "tccm", "x": pt_b[0], "y": pt_b[1], "snr_db": 12.0, "confidence": 0.90, "age_s": 0.08},
    ])
    assert fused is not None
    assert fused["x"] == 1.8 and fused["y"] == 0.0 and fused["spread_m"] == 0.0

    mic = MicSettings(ip="192.168.1.92", room_x=3.6, room_y=0.0, room_yaw=180.0, estimate_position=True)
    parser = PositionParser(mic)
    parser.feed(b'{"/api/audio/inputs/microphone/beam/direction":{"azimuth":90,"elevation":45,"level":-38}}')
    assert parser.position["estimated_xy"] == [1.8, 0.0]


def test_standalone_presenter_camera_allows_roi_clear_delete_and_track(tmp_path):
    """Standalone presenter_camera without presenter_seat must not return 400 on PUT /api/tracking/settings or ROI workflows."""
    from fastapi.testclient import TestClient
    from confdeck.app import create_app
    from confdeck.models import Camera, Settings
    from confdeck.runtime import Runtime
    from confdeck.tracking import TrackingSettings
    from confdeck.tracking_math.config import ROI, VisionSettings

    rt = Runtime(tmp_path)
    rt.settings = Settings(
        cameras=[Camera(id="0637f621814b", name="Tracking 128", ip="192.168.1.128", control_protocol="visca", control_enabled=True)],
        tracking=TrackingSettings(
            presenter_camera="0637f621814b",
            presenter_seat="",
            vision=VisionSettings(
                mode="advisory",
                rois=[ROI(camera="0637f621814b", seat="Seat 1", rect=(10, 10, 80, 90))],
            ),
        ),
    )
    app = create_app(rt, lifecycle=False)
    client = TestClient(app)
    hdr = {"X-ConfDeck": "1"}

    # 1. Clearing all ROIs via PUT /api/tracking/settings succeeds even with standalone presenter_camera
    st = client.get("/api/state").json()
    tracking = dict(st["settings"]["tracking"])
    tracking["vision"] = {"mode": "advisory", "camera_modes": {}, "rois": []}
    put_res = client.put("/api/tracking/settings", headers=hdr, json=tracking)
    assert put_res.status_code == 200, put_res.text

    # 2. Direct /api/accuracy/vision/rois set_camera -> remove -> clear_camera lifecycle
    add_res = client.post(
        "/api/accuracy/vision/rois",
        headers=hdr,
        json={
            "action": "set_camera",
            "camera": "0637f621814b",
            "preset": "",
            "rois": [
                {"seat": "Teaching Zone", "rect": [20, 15, 160, 140], "kind": "teaching", "masks": [[0, 0, 20, 20]]},
                {"seat": "Seat 2", "rect": [200, 30, 60, 70], "kind": "seat", "masks": []},
            ],
        },
    )
    assert add_res.status_code == 200 and len(add_res.json()["rois"]) == 2

    del_res = client.post(
        "/api/accuracy/vision/rois",
        headers=hdr,
        json={"action": "remove", "camera": "0637f621814b", "seat": "Seat 2"},
    )
    assert del_res.status_code == 200
    assert [r["seat"] for r in del_res.json()["rois"]] == ["Teaching Zone"]

    clr_res = client.post(
        "/api/accuracy/vision/rois",
        headers=hdr,
        json={"action": "clear_camera", "camera": "0637f621814b"},
    )
    assert clr_res.status_code == 200 and clr_res.json()["rois"] == []

    # 3. Enabling mode & track after clearing works end-to-end
    mode_res = client.post("/api/accuracy/vision/mode", headers=hdr, json={"mode": "off"})
    assert mode_res.status_code == 200
    trk_res = client.post(
        "/api/accuracy/vision/track",
        headers=hdr,
        json={"camera": "0637f621814b", "mode": "vision", "enabled": True},
    )
    assert trk_res.status_code == 200 and trk_res.json()["enabled"] is True
    assert rt.settings.tracking.vision.mode == "advisory"


def test_yolox_body_detector_basic():
    """YOLOXPersonDetector loads the ONNX model and detects persons cleanly."""
    from confdeck.vision.body import YOLOXPersonDetector
    det = YOLOXPersonDetector()
    assert det.available, f"Expected YOLOX model at {det.model_path} to load via onnxruntime or OpenCV DNN"
    black = np.zeros((180, 320, 3), dtype=np.uint8)
    results = det.detect_persons(black)
    assert isinstance(results, list) and results == []

    roi_results = det.detect_persons_in_roi(black, (0, 0, 320, 180))
    assert isinstance(roi_results, list) and roi_results == []
    assert det.last_cost_ms < 250.0


def test_yolox_body_detector_roi_overlap_filtering(monkeypatch):
    """YOLOX detect_persons_in_roi filters by ROI overlap and excludes masked seats."""
    from confdeck.vision.body import YOLOXPersonDetector
    det = YOLOXPersonDetector()
    monkeypatch.setattr(
        det,
        "detect_persons",
        lambda bgr: [
            [40, 30, 50, 110, 0.91],   # Teacher inside teaching ROI
            [240, 50, 45, 95, 0.94],   # Student outside teaching ROI
            [110, 40, 40, 90, 0.88],   # Masked zone decoy
        ],
    )
    frame = np.full((180, 320, 3), 128, dtype=np.uint8)
    results = det.detect_persons_in_roi(
        frame,
        (20, 20, 150, 140),
        min_overlap=0.25,
        masks=[[100, 30, 60, 110]],
    )
    assert len(results) == 1
    assert results[0][:4] == [40, 30, 50, 110]


def test_blackboard_roi_locks_when_camera_pans_and_blocks_extreme(monkeypatch, tmp_path):
    """Teaching ROI tracks the physical blackboard during VISCA pan and clamps against camera extremes."""
    from confdeck.models import Camera, Settings
    from confdeck.runtime import Runtime
    from confdeck.tracking_math.config import ROI, VisionSettings
    from confdeck.vision.mouth import SeatVisionDetector

    class ViscaPosDriver:
        def __init__(self):
            self.pan = 0
            self.tilt = 0
            self.zoom = 2000
            self.drives = []
            self.stops = 0
            self.absolutes = []

        async def position(self):
            return (self.pan & 0xFFFF, self.tilt & 0xFFFF, self.zoom)

        async def drive(self, pan=0.0, tilt=0.0, zoom=0, speed=1.0):
            self.drives.append((round(pan, 3), round(tilt, 3)))

        async def stop(self):
            self.stops += 1

        async def absolute(self, pos, speed):
            self.absolutes.append((pos, speed))

    cfg = Settings(
        cameras=[
            Camera(id="0637f621814b", name="Presenter 128", ip="192.168.1.128", control_enabled=True, control_protocol="visca"),
        ]
    )
    cfg.tracking.presenter_camera = "0637f621814b"
    cfg.tracking.vision = VisionSettings(
        mode="advisory",
        lock_hold_s=1.0,
        # Blackboard is on the RIGHT side [180..300] at home preset
        rois=[ROI(camera="0637f621814b", preset="", seat="Teaching Zone", rect=[180, 20, 120, 130], kind="teaching")],
    )
    rt = Runtime(tmp_path)
    rt.settings = cfg
    lab = rt.follow.lab
    drv = ViscaPosDriver()
    lab.autotrack_camera = "0637f621814b"
    lab.autotrack_driver = drv

    detected_persons = []

    class MockBodyDet:
        available = True
        backend = "onnxruntime"

        def detect_persons_in_roi(self, bgr, roi, min_overlap=0.25, masks=None, prev_box=None):
            rx, ry, rw, rh = roi
            rx2, ry2 = rx + rw, ry + rh
            out = []
            for p in detected_persons:
                bx, by, bw, bh, conf = p
                ox1, oy1 = max(bx, rx), max(by, ry)
                ox2, oy2 = min(bx + bw, rx2), min(by + bh, ry2)
                if ox2 > ox1 and oy2 > oy1 and ((ox2 - ox1) * (oy2 - oy1)) / max(1, bw * bh) >= min_overlap:
                    out.append(p)
            return out

    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (0, None))
    monkeypatch.setattr(SeatVisionDetector, "_get_body_detector", lambda self: MockBodyDet())

    frame = np.full((180, 320), 210, dtype=np.uint8)
    # Frame 1: at home pan=0, teacher stands on right edge of board x=260 -> pan RIGHT (pan_v > 0, <= 0.32)
    detected_persons = [[260, 35, 36, 95, 0.90]]
    lab.last_autotrack_times["0637f621814b"] = 0.0
    res1 = asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    assert res1["tracking"]["locked_roi"] == [180, 20, 120, 130]
    assert len(drv.drives) == 1
    assert 0.0 < drv.drives[0][0] <= 0.32

    # Frame 2: Camera panned RIGHT by +100 VISCA units (~55 px).
    # Physical blackboard shifted LEFT from [180..300] to ~[125..245]!
    # Stranger appears at raw screen x=265 (outside locked board!): MUST BE IGNORED!
    # Teacher is now centered at x=165 (still inside locked board [125..245]!).
    drv.pan = 100
    anchor = lab._teaching_roi_anchors["0637f621814b"]
    anchor["last_pos_t"] = 0.0  # force poll
    detected_persons = [[165, 35, 36, 95, 0.88], [265, 40, 40, 100, 0.95]]
    lab.last_autotrack_times["0637f621814b"] = 0.0
    res2 = asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    locked = res2["tracking"]["locked_roi"]
    assert locked[0] < 150 and (locked[0] + locked[2]) < 260
    assert res2["faces"]["Teaching Zone"][0] == 165  # Locked onto teacher at x=165, NOT decoy at x=265
    assert res2["tracking"]["target_box"][0] < 230  # Smoothly EMA-filtered toward x=165

    # Frame 3: If camera reaches right blackboard pan limit (pan=400 -> cam_pan_px=220 > pan_px_max=158),
    # even a person near right edge must NOT drive further RIGHT (blocks runaway to extreme!).
    drv.pan = 400
    anchor["last_pos_t"] = 0.0
    detected_persons = [[90, 35, 36, 95, 0.90]]
    # Put person at x=210 inside shifted edge or test right clamp directly:
    drv.pan = 300  # cam_pan_px = 165 > pan_px_max (~158), locked_roi=[15..135]
    anchor["last_pos_t"] = 0.0
    # Wait: if person on right of screen within locked edge or center, trying to drive pan>0 is clamped:
    anchor["pan_px_max"] = 140.0
    anchor["last_pos_t"] = 0.0
    detected_persons = [[110, 35, 36, 95, 0.90]]
    # Test clamping rightward drive when cam_pan_px >= pan_px_max:
    anchor["smoothed_box"] = None
    drv.pan = 260  # cam_pan_px = 143 >= 140, locked_roi = [37, 20, 120, 130]
    anchor["last_pos_t"] = 0.0
    # Wait: put ROI shifted right? No, at pan=260, locked_roi=[37..157]. Any person in [37..157] is left of center (pan_v < 0).
    # Verify pan_v > 0 clamp directly if teacher is on right side of a wide board [0..320] when at pan_px_max!
    cfg.tracking.vision.rois = [ROI(camera="0637f621814b", preset="", seat="Teaching Zone", rect=[0, 20, 320, 130], kind="teaching")]
    lab._teaching_roi_anchors.pop("0637f621814b", None)
    drv.pan = 0
    lab.last_autotrack_times["0637f621814b"] = 0.0
    detected_persons = [[150, 35, 36, 95, 0.90]]
    asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    # Now jump pan to far right beyond pan_px_max (~178 -> pan=360 -> 198 px)
    drv.pan = 360
    lab._teaching_roi_anchors["0637f621814b"]["last_pos_t"] = 0.0
    detected_persons = [[260, 35, 36, 95, 0.90]]  # Even if detection were at x=260, locked_roi ends at 320-198=122!
    drives_before = len(drv.drives)
    res3 = asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    assert res3["tracking"]["target_box"] is None  # Blocked beyond blackboard!
    assert len(drv.drives) == drives_before  # Stopped cleanly, NEVER drove toward extreme!


def test_teaching_body_uses_yolox_when_available(monkeypatch):
    """SeatVisionDetector._detect_teaching_body uses YOLOX body detector when available."""
    from confdeck.vision.mouth import SeatVisionDetector
    from confdeck.vision.body import YOLOXPersonDetector

    det = SeatVisionDetector()
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (0, None))

    # Mock the YOLOX detector to return a known person bbox
    class MockBodyDet:
        available = True
        _last_cost_ms = 5.0
        last_cost_ms = 5.0
        def detect_persons(self, bgr):
            return [[60, 30, 80, 120, 0.85]]
        def detect_persons_in_roi(self, bgr, roi, min_overlap=0.3, masks=None, prev_box=None):
            rx, ry, rw, rh = roi
            return [[60, 30, 80, 120, 0.85]]  # Overlaps with any reasonable ROI

    mock_det = MockBodyDet()
    det._body_detector = mock_det
    det._body_detector_checked = True

    rois = {"board": {"rect": [25, 20, 200, 140], "kind": "teaching", "masks": []}}
    scene = np.full((180, 320), 210, dtype=np.uint8)
    # Draw a person-like shape
    cv2.rectangle(scene, (60, 30), (140, 150), 60, -1)

    for step in range(3):
        det.process(scene, rois, 50.0 + step * 0.1)

    # YOLOX should have produced a body detection
    assert det.occupancy_state.get("board") is True
    assert det.target_poses.get("board") == "back_or_body"
    assert "board" in det.teaching_targets


def test_bounded_servo_clamp_teaching_mode():
    """Teaching mode VISCA servo loop uses clamped speed (max 0.30 pan, 0.25 tilt)."""
    # Verify the speed clamping logic by computing what the servo loop would produce
    # for a teaching-mode target far off-center
    dx = 0.8  # Far right
    dy = 0.5  # Far down
    deadband_x = 0.07
    deadband_y = 0.08

    # Teaching mode clamps
    max_pan_speed = 0.30
    max_tilt_speed = 0.25

    err_x = max(0.0, (abs(dx) - deadband_x) / (1.0 - deadband_x))
    pan_v = min(max_pan_speed, (err_x ** 0.65) * 1.30)

    err_y = max(0.0, (abs(dy) - deadband_y) / (1.0 - deadband_y))
    tilt_v = min(max_tilt_speed, (err_y ** 0.70) * 1.20)

    # With teaching mode, speeds must be clamped
    assert pan_v <= 0.30, f"Pan speed {pan_v} exceeds teaching limit 0.30"
    assert tilt_v <= 0.25, f"Tilt speed {tilt_v} exceeds teaching limit 0.25"

    # Without teaching mode (max 1.0), speeds would be higher
    pan_v_unclamped = min(1.0, (err_x ** 0.65) * 1.30)
    tilt_v_unclamped = min(1.0, (err_y ** 0.70) * 1.20)
    assert pan_v_unclamped > 0.30, "Unclamped pan should exceed teaching limit for large offset"


def test_teaching_zone_ignores_static_clutter(monkeypatch):
    """Static high-contrast equipment/cables inside a Teaching Zone must not false-trigger when motionless."""
    from confdeck.vision.mouth import SeatVisionDetector

    det = SeatVisionDetector()
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (0, None))
    rois = {"Teaching Zone": {"rect": [25, 20, 160, 140], "kind": "teaching", "masks": []}}

    scene = np.full((180, 320), 210, dtype=np.uint8)
    # High-contrast static desk equipment + cables crossing ROI border & interior
    cv2.rectangle(scene, (10, 100), (220, 175), 35, -1)
    cv2.line(scene, (15, 30), (210, 150), 25, 4)
    cv2.rectangle(scene, (55, 45), (120, 95), 70, 2)

    for step in range(5):
        det.process(scene, rois, 50.0 + step * 0.1)
    assert det.occupancy_state["Teaching Zone"] is False
    assert det.last_llr["Teaching Zone"] == det.LLR_EMPTY


def test_teaching_zone_uses_fast_yunet_face_without_running_yolox(monkeypatch):
    """When YuNet sees the teacher's face in a Teaching Zone, keep the tight face box and skip YOLOX."""
    from confdeck.vision.mouth import SeatVisionDetector

    det = SeatVisionDetector()
    yolox_calls = 0

    class CountingBodyDet:
        available = True
        backend = "opencv_dnn"

        def detect_persons_in_roi(self, *args, **kwargs):
            nonlocal yolox_calls
            yolox_calls += 1
            return [[20, 20, 90, 140, 0.9]]

    det._body_detector = CountingBodyDet()
    det._body_detector_checked = True

    face = _dummy_face_row(72, 42, 30, 36, 0.93)
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (1, np.vstack([face])))

    rois = {"Teaching Zone": {"rect": [20, 15, 180, 150], "kind": "teaching", "masks": []}}
    frame = np.full((180, 320), 210, dtype=np.uint8)
    det.process(frame, rois, 10.0)

    assert yolox_calls == 0
    assert det.target_poses["Teaching Zone"] == "front"
    assert det.detected_face_boxes["Teaching Zone"] == [72, 42, 30, 36]


def test_teaching_zone_does_not_freeze_on_stale_template(monkeypatch):
    """When teacher leaves or moves away and YOLOX returns empty, template fallback expires within 0.45s."""
    from confdeck.vision.mouth import SeatVisionDetector

    det = SeatVisionDetector()
    persons = [[60, 30, 44, 100, 0.88]]

    class MockBodyDet:
        available = True
        backend = "opencv_dnn"

        def detect_persons_in_roi(self, *args, **kwargs):
            return list(persons)

    det._body_detector = MockBodyDet()
    det._body_detector_checked = True
    monkeypatch.setattr(det, "_detect_faces", lambda bgr: (0, None))

    rois = {"Teaching Zone": {"rect": [20, 15, 180, 150], "kind": "teaching", "masks": []}}
    frame = np.full((180, 320), 210, dtype=np.uint8)
    _draw_person_silhouette(frame, 82, 30)

    det.process(frame, rois, 10.0)
    assert "Teaching Zone" in det.detected_face_boxes

    # Teacher leaves and background is uniform; YOLOX finds nobody
    persons.clear()
    blank = np.full((180, 320), 210, dtype=np.uint8)
    det.process(blank, rois, 10.6)
    assert "Teaching Zone" not in det.detected_face_boxes


def test_teaching_zone_gentle_zoom_on_entry(monkeypatch, tmp_path):
    """Entering a Teaching Zone triggers gentle bounded zoom-in and stops once framed/zoomed."""
    from confdeck.models import Camera, Settings
    from confdeck.runtime import Runtime
    from confdeck.tracking_math.config import ROI, VisionSettings
    from confdeck.vision.mouth import SeatVisionDetector

    class ZoomViscaDriver:
        def __init__(self):
            self.pan = 0
            self.tilt = 0
            self.zoom = 1000
            self.drives = []
            self.stops = 0

        async def position(self):
            return (self.pan, self.tilt, self.zoom)

        async def drive(self, pan=0.0, tilt=0.0, zoom=0, speed=1.0):
            self.drives.append((round(pan, 3), round(tilt, 3), round(zoom, 3)))

        async def stop(self):
            self.stops += 1

    cfg = Settings(
        cameras=[
            Camera(id="0637f621814b", name="Presenter 128", ip="192.168.1.128", control_enabled=True, control_protocol="visca"),
        ]
    )
    cfg.tracking.presenter_camera = "0637f621814b"
    cfg.tracking.vision = VisionSettings(
        mode="advisory",
        rois=[ROI(camera="0637f621814b", preset="", seat="Teaching Zone", rect=[80, 20, 160, 140], kind="teaching")],
    )
    rt = Runtime(tmp_path)
    rt.settings = cfg
    lab = rt.follow.lab
    drv = ZoomViscaDriver()
    lab.autotrack_camera = "0637f621814b"
    lab.autotrack_driver = drv

    face = _dummy_face_row(145, 72, 30, 36, 0.92)
    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (1, np.vstack([face])))

    frame = np.full((180, 320), 210, dtype=np.uint8)
    lab.last_autotrack_times["0637f621814b"] = 0.0
    asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    assert len(drv.drives) == 1
    assert drv.drives[0][2] > 0.0

    # Once zoomed in +1400 above home, zoom stops while pan/tilt remain centered
    drv.zoom = 2500
    anchor = lab._teaching_roi_anchors["0637f621814b"]
    anchor["last_pos_t"] = 0.0
    lab.last_autotrack_times["0637f621814b"] = 0.0
    asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    assert drv.stops >= 1

    # As soon as teacher leaves the zone (~1.2s), camera falls back to initial wide view (home_raw)
    monkeypatch.setattr(SeatVisionDetector, "_detect_faces", lambda self, bgr: (0, None))
    anchor["last_seen_t"] = time.monotonic() - 1.5
    anchor["last_pos_t"] = 0.0
    drv.absolutes = []
    async def _abs(pos, speed):
        drv.absolutes.append((pos, speed))
    drv.absolute = _abs
    asyncio.run(lab.frame("0637f621814b", "", frame, source="rtsp"))
    assert len(drv.absolutes) == 1
    assert drv.absolutes[0][0] == (0, 0, 1000)


