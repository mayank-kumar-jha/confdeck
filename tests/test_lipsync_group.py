"""Unit & benchmark tests for 106-pt neural lip-sync + super-seat group speaker lock."""
import time
import numpy as np
import pytest

from confdeck.vision.lipsync import (
    DEFAULT_2D106_PATH,
    GroupSpeakerSelector,
    LipLandmark106Model,
)
from confdeck.vision.mouth import SeatVisionDetector, DEFAULT_YUNET_PATH


def test_2d106det_model_present_and_loads():
    assert DEFAULT_2D106_PATH.is_file(), f"2d106det.onnx missing at {DEFAULT_2D106_PATH}"
    lm = LipLandmark106Model()
    assert lm.available is True


def test_group_speaker_lock_and_zoom_cycle():
    # Disable ONNX dependency on synthetic flat rectangles so fallback flux cleanly isolates cadence
    selector = GroupSpeakerSelector(
        confirm_frames=3,
        entry_prob=0.52,
        target_face_h_px=48.0,
        max_zoom_in_s=1.0,
    )
    selector.scorer.landmarks.session = None

    base_gray = np.full((180, 320), 130, dtype=np.uint8)
    # 3 faces in wide group shot:
    # Person A (left, small face 26px tall, actively speaking)
    # Person B (center, larger face 32px tall, silent)
    # Person C (right, small face 26px tall, silent)
    f_a = np.array([40, 60, 24, 26, 47, 68, 57, 68, 52, 74, 48, 80, 56, 80, 0.95], dtype=np.float32)
    f_b = np.array([144, 56, 30, 32, 153, 66, 165, 66, 159, 73, 154, 80, 164, 80, 0.98], dtype=np.float32)
    f_c = np.array([245, 60, 24, 26, 252, 68, 262, 68, 257, 74, 253, 80, 261, 80, 0.94], dtype=np.float32)

    t = 10.0
    locked_res = None
    for i in range(10):
        frame = base_gray.copy()
        # Person A oscillates mouth opening at speech cadence while B and C remain still
        if i % 2 == 0:
            frame[78:84, 46:58] = 25
        else:
            frame[79:81, 46:58] = 110
        locked_res = selector.update([f_a, f_b, f_c], frame, t)
        t += 0.12

    assert locked_res is not None
    assert locked_res["reason"] == "lip_sync_locked"
    assert locked_res["target_box"] is not None
    # Must lock Person A (x ~ 40), NOT larger silent Person B (x ~ 144)!
    assert abs(locked_res["target_box"][0] - 40) <= 5
    # Centered? Not yet (cx=52 vs 160), so zoom shouldn't blindly zoom in before centering
    assert locked_res["zoom_action"] == "none"

    # Once camera centers Person A at x=148 (cx=160, fh=26 < 48), zoom_action should be 'in'
    f_a_centered = np.array([148, 60, 24, 26, 155, 68, 165, 68, 160, 74, 156, 80, 164, 80, 0.95], dtype=np.float32)
    # Match movement continuously near center
    selector.tracks[locked_res["locked_track_id"]].box = [148.0, 60.0, 24.0, 26.0]
    res_zoom_in = selector.update([f_a_centered], base_gray, t)
    assert res_zoom_in["zoom_action"] == "in"

    # Once zoomed in so face height reaches 50px (>= 48), zoom_action stops cleanly
    f_a_zoomed = np.array([138, 50, 44, 50, 150, 65, 170, 65, 160, 75, 152, 88, 168, 88, 0.95], dtype=np.float32)
    res_zoom_stop = selector.update([f_a_zoomed], base_gray, t + 0.1)
    assert res_zoom_stop["zoom_action"] == "stop"

    # New seat lock -> reset() clears speaker lock and zoom budget
    selector.reset()
    assert selector.locked_track_id is None
    assert selector.zoom_used_s == 0.0


def test_cpu_budget_three_faces_with_yunet_and_2d106():
    det = SeatVisionDetector(str(DEFAULT_YUNET_PATH))
    assert det.group_selector is not None and det.group_selector.scorer.landmarks.available

    hi_res = np.full((720, 1280, 3), 140, dtype=np.uint8)
    ref = np.full((180, 320), 140, dtype=np.uint8)
    faces = [
        np.array([40, 60, 24, 26, 47, 68, 57, 68, 52, 74, 48, 80, 56, 80, 0.95], dtype=np.float32),
        np.array([144, 56, 30, 32, 153, 66, 165, 66, 159, 73, 154, 80, 164, 80, 0.98], dtype=np.float32),
        np.array([245, 60, 24, 26, 252, 68, 262, 68, 257, 74, 253, 80, 261, 80, 0.94], dtype=np.float32),
    ]
    det._detect_faces = lambda bgr: (len(faces), np.array(faces))

    # Warm up ONNX session
    det.process(ref, {"group": {"rect": [0, 0, 320, 180]}}, 1.0, hi_res_frame=hi_res)

    t0 = time.perf_counter()
    for i in range(10):
        det.process(ref, {"group": {"rect": [0, 0, 320, 180]}}, 1.1 + i * 0.08, hi_res_frame=hi_res)
    avg_ms = (time.perf_counter() - t0) * 1000.0 / 10.0
    print(f"3-face YuNet + 2d106det average frame time: {avg_ms:.2f} ms")
    assert det.disabled is False
    assert avg_ms < 120.0
