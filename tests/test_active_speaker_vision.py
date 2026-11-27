import time
import pytest
import numpy as np
from pathlib import Path

from confdeck.vision.mouth import MouthMotion, SeatVisionDetector, DEFAULT_YUNET_PATH


def test_yunet_model_present_and_loads():
    assert DEFAULT_YUNET_PATH.is_file(), f"Model file missing at {DEFAULT_YUNET_PATH}"
    motion = MouthMotion(str(DEFAULT_YUNET_PATH))
    assert motion.detector is not None


def test_seat_vision_detector_empty_frame():
    detector = SeatVisionDetector(str(DEFAULT_YUNET_PATH))
    rois = {
        "seat1": {"rect": [100, 40, 100, 100]},
        "seat2": {"rect": [0, 0, 80, 80]},
    }
    dummy = np.zeros((180, 320), dtype=np.uint8)
    now = time.monotonic()
    scores = detector.process(dummy, rois, now)

    # Empty frame must mark all seats unoccupied with -4.0 LLR veto
    assert detector.occupancy_state["seat1"] is False
    assert detector.occupancy_state["seat2"] is False
    assert detector.last_llr["seat1"] == -4.0
    assert detector.last_llr["seat2"] == -4.0
    assert scores["seat1"] == 0.0
    assert scores["seat2"] == 0.0


def test_seat_vision_detector_face_occupancy_and_hysteresis():
    detector = SeatVisionDetector(str(DEFAULT_YUNET_PATH), hold_seconds=2.0)
    # Define ROI covering middle of frame
    rois = {
        "center_seat": {"rect": [120, 30, 100, 100]},
        "side_seat": {"rect": [0, 0, 80, 80]},
    }

    # Synthesize realistic gradient face patch
    frame = np.full((180, 320, 3), 120, dtype=np.uint8)
    
    # Mock face detection result directly to test downstream geometry & hysteresis
    import cv2
    t0 = 100.0
    # Process blank frame first
    detector.process(frame, rois, t0)
    assert detector.occupancy_state["center_seat"] is False

    # Simulate face detected in center_seat ROI
    mock_face = np.array([
        140.0, 50.0, 60.0, 70.0,  # x, y, w, h
        155.0, 70.0,              # right eye
        185.0, 70.0,              # left eye
        170.0, 85.0,              # nose
        158.0, 95.0,              # right mouth
        182.0, 95.0,              # left mouth
        0.95                      # score
    ], dtype=np.float32)

    # Inject detection via method override
    detector._detect_faces = lambda img: (1, np.array([mock_face]))
    detector.process(frame, rois, t0 + 0.2)
    assert detector.occupancy_state["center_seat"] is True
    assert detector.occupancy_state["side_seat"] is False
    assert detector.last_llr["center_seat"] in (-0.5, 3.5)
    assert detector.last_llr["side_seat"] == -4.0  # Empty seat vetoed!

    # Test 2.0s hold hysteresis: face temporarily not detected (head turn)
    detector._detect_faces = lambda img: (1, None)
    detector.process(frame, rois, t0 + 1.0)
    # Still occupied because 1.0s < 2.0s hold time
    assert detector.occupancy_state["center_seat"] is True

    # After hold time expires:
    detector.process(frame, rois, t0 + 2.5)
    assert detector.occupancy_state["center_seat"] is False
    assert detector.last_llr["center_seat"] == -4.0


def test_seat_vision_detector_mouth_dynamics():
    detector = SeatVisionDetector(str(DEFAULT_YUNET_PATH), speech_threshold=0.10)
    rois = {"seat1": {"rect": [120, 30, 100, 100]}}

    mock_face = np.array([
        140.0, 50.0, 60.0, 70.0,
        155.0, 70.0, 185.0, 70.0,
        170.0, 85.0,
        158.0, 95.0, 182.0, 95.0,
        0.95
    ], dtype=np.float32)
    detector._detect_faces = lambda img: (1, np.array([mock_face]))

    frame = np.full((180, 320), 120, dtype=np.uint8)

    # 1. Simulate static / silent mouth: constant texture
    t = 100.0
    for _ in range(5):
        detector.process(frame, rois, t)
        t += 0.2

    # Should be occupied but quiet
    assert detector.occupancy_state["seat1"] is True
    assert detector.speaking_state["seat1"] is False
    assert detector.last_llr["seat1"] == -0.5

    # 2. Simulate speech oscillation: alternating mouth opening
    for i in range(5):
        # Draw high-contrast horizontal lips when open
        f = frame.copy()
        if i % 2 == 0:
            f[93:97, 155:185] = 20  # dark inner oral cavity
        detector.process(f, rois, t)
        t += 0.2

    # Varied mouth aperture should trigger speaking state
    assert detector.occupancy_state["seat1"] is True
    assert detector.speaking_state["seat1"] is True
    assert detector.last_llr["seat1"] == 3.5  # Confirmed speaking boost!


@pytest.mark.asyncio
async def test_lab_frame_integration_with_dual_camera():
    from types import SimpleNamespace
    from confdeck.evaluation.lab import AccuracyLab

    # Mock runtime with Camera 128 as vision overview and Camera 108 as PTZ
    runtime = SimpleNamespace(
        root=Path("."),
        settings=SimpleNamespace(
            tracking=SimpleNamespace(
                settle_ms=500,
                vision=SimpleNamespace(
                    mode="active",
                    camera_modes={"128": "active", "108": "off"},
                    rois=[
                        SimpleNamespace(camera="128", preset="", seat="seat1", rect=[120, 30, 100, 100]),
                        SimpleNamespace(camera="128", preset="", seat="seat2", rect=[0, 0, 80, 80]),
                    ],
                    llr_models={},
                    mouth_model=str(DEFAULT_YUNET_PATH),
                ),
            ),
            cameras=[
                SimpleNamespace(id="128", control_enabled=False),
                SimpleNamespace(id="108", control_enabled=True),
            ],
        ),
        follow=SimpleNamespace(
            engine=SimpleNamespace(candidate=None, current=None),
        ),
        manual=SimpleNamespace(leases=set()),
    )

    lab = AccuracyLab(runtime)
    dummy = np.zeros((180, 320), dtype=np.uint8)

    # Camera 108 is moving (PTZ), but Camera 128 is static
    lab.moving_until["108"] = time.monotonic() + 10.0

    # Process frame on Camera 128
    result = await lab.frame("128", "", dummy)
    assert result["mode"] == "active"
    # Seat 1 & 2 are empty in blank frame -> LLR must be -4.0 (veto)
    assert result["scores"]["seat1"] == -4.0
    assert result["scores"]["seat2"] == -4.0

    # Active scores should reflect Camera 128's scores
    active = lab.active_scores()
    assert active["seat1"] == -4.0
    assert active["seat2"] == -4.0
