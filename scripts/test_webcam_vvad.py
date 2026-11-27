"""Interactive real-time webcam validation tool for ConfDeck V-VAD & Mouth Dynamics.

Runs the actual commercial SeatVisionDetector from confdeck.vision.mouth on the
local laptop camera at 320x180 resolution to verify that head nods, tilts,
and turns do NOT trip false speaking=True, while natural speech is detected
swiftly and reliably.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Ensure root directory is on PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from confdeck.vision.mouth import SeatVisionDetector, DEFAULT_YUNET_PATH


def main():
    print("=" * 65)
    print(" ConfDeck Live Webcam Active Speaker & Head-Motion Validator")
    print("=" * 65)
    print(f"Using YuNet ONNX: {DEFAULT_YUNET_PATH.name}")

    if not DEFAULT_YUNET_PATH.is_file():
        print(f"ERROR: Model file missing at {DEFAULT_YUNET_PATH}")
        return 1

    # Initialize webcam
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Could not open laptop webcam (index 0).")
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Initialize the actual ConfDeck detector
    detector = SeatVisionDetector(
        model_path=str(DEFAULT_YUNET_PATH),
        speech_threshold=2.00,
        quiet_threshold=1.10,
        speech_entry_frames=2,
        speech_hangover_frames=3,
        upper_motion_weight=1.20,
    )

    # Seat ROI covering the field of view on 320x180
    rois = {"seat": {"rect": [10, 10, 300, 160]}}

    window_name = "ConfDeck V-VAD Live Webcam Validator (Press Q to Exit)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 960, 540)

    print("\nControls:")
    print("  [Q] or [ESC] : Quit")
    print("  [R]          : Reset detector tracking")
    print("  [+] / [-]    : Adjust speech threshold (default: 2.50)")
    print("  [W] / [S]    : Adjust upper motion rejection weight (default: 1.35)")
    print("\nHow to test:")
    print("  1. Nod / shake / tilt your head -> Verify energy stays LOW (< 1.2) and box stays GREEN.")
    print("  2. Speak normally              -> Verify energy rises (> 2.5) and box turns RED (SPEAKER).")
    print("=" * 65)

    last_time = time.monotonic()
    frame_interval = 0.10  # Evaluate at 10 Hz for smooth responsive feedback
    last_eval_time = 0.0

    energy = 0.0
    speaking = False
    occupied = False
    proc_cost_ms = 0.0
    scores = {}

    while True:
        ret, raw_bgr = cap.read()
        if not ret or raw_bgr is None:
            time.sleep(0.01)
            continue

        now = time.monotonic()

        # Downsample to 320x180 (exact resolution used in ConfDeck overview stream)
        frame_180p = cv2.resize(raw_bgr, (320, 180), interpolation=cv2.INTER_AREA)

        # Run vision detector at cadence
        if now - last_eval_time >= frame_interval:
            t0 = time.perf_counter()
            scores = detector.process(frame_180p, rois, now)
            proc_cost_ms = (time.perf_counter() - t0) * 1000.0
            last_eval_time = now

            energy = detector.speech_energy.get("seat", 0.0)
            speaking = detector.speaking_state.get("seat", False)
            occupied = detector.occupancy_state.get("seat", False)

        # Create display frame (upscaled for clear desktop rendering)
        disp = cv2.resize(frame_180p, (960, 540), interpolation=cv2.INTER_LINEAR)
        scale_x = 960.0 / 320.0
        scale_y = 540.0 / 180.0

        # Draw detected face boxes
        face_box = detector.detected_face_boxes.get("seat")
        if face_box is not None:
            fx, fy, fw, fh = [int(v) for v in face_box]
            dx1 = int(round(fx * scale_x))
            dy1 = int(round(fy * scale_y))
            dx2 = int(round((fx + fw) * scale_x))
            dy2 = int(round((fy + fh) * scale_y))

            if speaking:
                box_color = (40, 40, 240)  # Bright Red
                status_label = f"SPEAKER ACTIVE  (Energy: {energy:.2f})"
                bg_color = (40, 40, 220)
            elif occupied:
                box_color = (50, 205, 50)  # Vibrant Lime Green
                status_label = f"PRESENT - SILENT  (Energy: {energy:.2f})"
                bg_color = (30, 160, 30)
            else:
                box_color = (180, 180, 180)
                status_label = "EMPTY"
                bg_color = (100, 100, 100)

            # Draw face rectangle
            cv2.rectangle(disp, (dx1, dy1), (dx2, dy2), box_color, 3)

            # Draw top banner badge
            tag_w = max(240, len(status_label) * 11 + 20)
            tag_y1 = max(0, dy1 - 28)
            cv2.rectangle(disp, (dx1, tag_y1), (dx1 + tag_w, dy1), bg_color, -1)
            cv2.putText(
                disp, status_label, (dx1 + 8, dy1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA
            )
        else:
            cv2.putText(
                disp, "LOOK AT CAMERA TO DETECT FACE", (30, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2, cv2.LINE_AA
            )

        # Render Top Dashboard Panel
        cv2.rectangle(disp, (0, 0), (960, 52), (18, 18, 24), -1)
        cv2.line(disp, (0, 52), (960, 52), (50, 50, 60), 1)

        # Title
        cv2.putText(
            disp, "ConfDeck Commercial V-VAD", (15, 32),
            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA
        )

        # Status Pill
        if speaking:
            pill_text = "SPEAKING"
            pill_bg = (40, 40, 220)
        elif occupied:
            pill_text = "PRESENT"
            pill_bg = (30, 160, 30)
        else:
            pill_text = "EMPTY"
            pill_bg = (80, 80, 80)

        cv2.rectangle(disp, (320, 10), (430, 42), pill_bg, -1)
        cv2.putText(
            disp, pill_text, (330, 32),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA
        )

        # Parameters display
        param_text = f"Thresh: {detector.speech_threshold:.2f} | Weight: {detector.upper_motion_weight:.2f} | CPU: {proc_cost_ms:.1f}ms"
        cv2.putText(
            disp, param_text, (450, 32),
            cv2.FONT_HERSHEY_SIMPLEX, 0.50, (200, 200, 200), 1, cv2.LINE_AA
        )

        # Render Bottom Energy Gauge
        cv2.rectangle(disp, (0, 480), (960, 540), (18, 18, 24), -1)
        cv2.line(disp, (0, 480), (960, 480), (50, 50, 60), 1)

        cv2.putText(
            disp, f"Oral Motion Energy: {energy:.2f}", (15, 515),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA
        )

        # Meter background bar
        meter_x1, meter_y1 = 280, 498
        meter_w, meter_h = 450, 22
        cv2.rectangle(disp, (meter_x1, meter_y1), (meter_x1 + meter_w, meter_y1 + meter_h), (40, 40, 45), -1)
        cv2.rectangle(disp, (meter_x1, meter_y1), (meter_x1 + meter_w, meter_y1 + meter_h), (80, 80, 90), 1)

        # Fill meter
        max_energy = 10.0
        fill_ratio = min(1.0, max(0.0, energy / max_energy))
        fill_w = int(fill_ratio * meter_w)
        fill_color = (40, 40, 240) if speaking else (50, 190, 50)
        if fill_w > 0:
            cv2.rectangle(disp, (meter_x1, meter_y1), (meter_x1 + fill_w, meter_y1 + meter_h), fill_color, -1)

        # Threshold marker line on gauge
        thresh_x = int(meter_x1 + (detector.speech_threshold / max_energy) * meter_w)
        cv2.line(disp, (thresh_x, meter_y1 - 3), (thresh_x, meter_y1 + meter_h + 3), (0, 230, 255), 2)
        cv2.putText(
            disp, f"Threshold ({detector.speech_threshold:.1f})", (thresh_x - 35, meter_y1 - 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 230, 255), 1, cv2.LINE_AA
        )

        # Instructions hint
        cv2.putText(
            disp, "[Q] Quit  [R] Reset  [+/-] Threshold  [W/S] Weight", (750, 515),
            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1, cv2.LINE_AA
        )

        cv2.imshow(window_name, disp)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q'), 27):  # Q or ESC
            break
        elif key in (ord('r'), ord('R')):
            detector.reset()
            print("Detector state reset.")
        elif key in (ord('+'), ord('=')):
            detector.speech_threshold = round(detector.speech_threshold + 0.20, 2)
            print(f"Speech threshold raised to {detector.speech_threshold:.2f}")
        elif key in (ord('-'), ord('_')):
            detector.speech_threshold = max(0.50, round(detector.speech_threshold - 0.20, 2))
            print(f"Speech threshold lowered to {detector.speech_threshold:.2f}")
        elif key in (ord('w'), ord('W')):
            detector.upper_motion_weight = round(detector.upper_motion_weight + 0.10, 2)
            print(f"Upper motion weight raised to {detector.upper_motion_weight:.2f}")
        elif key in (ord('s'), ord('S')):
            detector.upper_motion_weight = max(0.50, round(detector.upper_motion_weight - 0.10, 2))
            print(f"Upper motion weight lowered to {detector.upper_motion_weight:.2f}")

    cap.release()
    cv2.destroyAllWindows()
    print("\nWebcam validator closed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
