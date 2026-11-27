"""Lightweight, low-latency face occupancy and active speaker mouth detector.

Supports ultra-fast OpenCV YuNet ONNX (330 KB, ~4 ms execution on CPU) for
full-frame seat occupancy tracking and mouth dynamics, with cranial-anchored
face extraction, lip aperture dynamics, contrast normalization, 3D head-motion
rejection, and optional MediaPipe Tasks fallback.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np

DEFAULT_YUNET_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "vision-model" / "face_detection_yunet_2023mar.onnx"


class MouthMotion:
    """Single-crop mouth motion scorer supporting YuNet ONNX, MediaPipe, and crop fallback."""

    def __init__(self, model_path: str | Path | None = None, smoothing: float = 0.5, threshold: float = 2.0) -> None:
        self.smoothing = float(np.clip(smoothing, 0.0, 0.99))
        self.threshold = float(threshold)
        self._prev_gray: np.ndarray | None = None
        self._energy: float = 0.0
        self.window = deque(maxlen=5)

        self.model_path = Path(model_path) if model_path else DEFAULT_YUNET_PATH
        self.detector = None
        self.mp_detector = None
        self.landmark106 = None
        self._landmark_mar_hist: deque[float] = deque(maxlen=8)

        # 0. Commercial InsightFace 2d106det.onnx 106-point lip landmark regressor (ONNX Runtime CPU)
        try:
            from confdeck.vision.lipsync import LipLandmark106Model
            lm = LipLandmark106Model()
            if lm.available:
                self.landmark106 = lm
        except Exception:
            self.landmark106 = None

        # 1. Try OpenCV FaceDetectorYN (YuNet ONNX)
        if self.model_path.is_file() and (str(self.model_path).endswith(".onnx") or not str(self.model_path).endswith(".task")):
            try:
                if hasattr(cv2, "FaceDetectorYN"):
                    self.detector = cv2.FaceDetectorYN.create(
                        str(self.model_path), "", (320, 180), score_threshold=0.4
                    )
            except Exception:
                self.detector = None

        # 2. Try MediaPipe if model is a .task or mediapipe is requested
        if self.detector is None and self.model_path.is_file():
            try:
                import mediapipe as mp
                self.mp_detector = mp.tasks.vision.FaceLandmarker.create_from_options(
                    mp.tasks.vision.FaceLandmarkerOptions(
                        base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model_path)),
                        output_face_blendshapes=True, num_faces=1
                    )
                )
            except Exception:
                self.mp_detector = None

    def _detect_faces(self, bgr: np.ndarray) -> tuple[int, np.ndarray | None]:
        if self.detector is not None:
            return self.detector.detect(bgr)
        return 0, None

    def reset(self) -> None:
        self._prev_gray = None
        self._energy = 0.0
        self.window.clear()
        self._landmark_mar_hist.clear()

    def score(self, grayscale_crop: Any) -> float | None:
        """Score oral motion from a single cropped grayscale or BGR image."""
        crop = np.asarray(grayscale_crop, dtype=np.uint8)
        if crop.size == 0:
            return None

        # 0. Commercial InsightFace 2d106det neural MAR scoring when face crop is clear
        if self.landmark106 is not None and min(crop.shape[:2]) >= 24:
            try:
                h, w = crop.shape[:2]
                res = self.landmark106.predict(crop, [w * 0.1, h * 0.1, w * 0.8, h * 0.8])
                if res is not None:
                    self._landmark_mar_hist.append(res["inner_mar"])
                    if len(self._landmark_mar_hist) >= 3:
                        mar_arr = np.asarray(self._landmark_mar_hist, dtype=np.float32)
                        return float(np.std(mar_arr) * 25.0 + abs(mar_arr[-1] - mar_arr[-2]) * 10.0)
                    return None
            except Exception:
                pass

        # MediaPipe path
        if self.mp_detector is not None:
            try:
                import mediapipe as mp
                rgb = np.repeat(crop[..., None], 3, axis=2)
                result = self.mp_detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                if not result.face_blendshapes:
                    return None
                values = [
                    c.score for c in result.face_blendshapes[0]
                    if c.category_name.startswith("mouth") or c.category_name == "jawOpen"
                ]
                self.window.append(values)
                return float(np.std(np.asarray(self.window), axis=0).mean()) if len(self.window) >= 3 else None
            except Exception:
                return None

        # OpenCV YuNet path on crop
        if self.detector is not None:
            h, w = crop.shape[:2]
            if h < 10 or w < 10:
                return None
            bgr = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR) if crop.ndim == 2 else crop
            self.detector.setInputSize((w, h))
            ret, faces = self._detect_faces(bgr)
            if faces is None or len(faces) == 0:
                return None
            f = faces[0]
            rm_x, rm_y = f[10], f[11]
            lm_x, lm_y = f[12], f[13]
            n_x, n_y = f[8], f[9]
            mc_x, mc_y = (rm_x + lm_x) / 2.0, (rm_y + lm_y) / 2.0
            mouth_w = max(8.0, float(np.hypot(lm_x - rm_x, lm_y - rm_y)))
            mouth_h = max(5.0, float(np.hypot(mc_x - n_x, mc_y - n_y) * 0.9))
            x1 = int(max(0, mc_x - mouth_w * 0.6))
            x2 = int(min(w, mc_x + mouth_w * 0.6))
            y1 = int(max(0, mc_y - mouth_h * 0.5))
            y2 = int(min(h, mc_y + mouth_h * 0.6))
            gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            mouth_crop = gray[y1:y2, x1:x2]
            if mouth_crop.size == 0:
                return None
            gy = cv2.Sobel(mouth_crop, cv2.CV_32F, 0, 1, ksize=3)
            gx = cv2.Sobel(mouth_crop, cv2.CV_32F, 1, 0, ksize=3)
            aperture = float(np.mean(np.abs(gy)) / (np.mean(np.abs(gx)) + 1e-5))
            self.window.append(aperture)
            return float(np.std(list(self.window))) if len(self.window) >= 3 else None

        # Fallback to normalized difference if no detector
        return self.compute(crop, (0, 0, crop.shape[1], crop.shape[0]))

    def compute(self, gray: np.ndarray, bbox: list[int] | tuple[int, int, int, int]) -> float:
        """Fallback crop-based motion estimator for arbitrary bounding boxes."""
        if gray is None or gray.size == 0 or len(bbox) < 4:
            self._energy *= self.smoothing
            return float(self._energy)

        h, w = gray.shape[:2]
        x, y, bw, bh = [int(v) for v in bbox[:4]]
        x1 = max(0, min(w - 1, x))
        y1 = max(0, min(h - 1, y))
        x2 = max(x1 + 1, min(w, x + bw))
        y2 = max(y1 + 1, min(h, y + bh))

        crop = cv2.resize(gray[y1:y2, x1:x2], (32, 40), interpolation=cv2.INTER_AREA).astype(np.float32)
        mean = float(np.mean(crop))
        std = float(np.std(crop))
        norm = (crop - mean) / max(std, 8.0)

        if self._prev_gray is None or self._prev_gray.shape != norm.shape:
            self._prev_gray = norm
            self._energy = 0.0
            return 0.0

        diff = float(np.mean(np.abs(norm[20:38, 6:26] - self._prev_gray[20:38, 6:26]))) * 10.0
        self._prev_gray = norm
        self._energy = self.smoothing * self._energy + (1.0 - self.smoothing) * diff
        return float(self._energy)

    @property
    def is_moving(self) -> bool:
        return self._energy >= self.threshold

    def close(self) -> None:
        if self.mp_detector is not None:
            try:
                self.mp_detector.close()
            except Exception:
                pass


class SeatVisionDetector:
    """Commercial-grade active speaker & multi-seat occupancy detector.

    Dual-Stream V-VAD Architecture:
    1. Rigid Cranial-Anchored face extraction (eyes-locked, completely decoupling
       skull alignment from mandible/jaw drop during speech).
    2. Lip Aperture Dynamics (Sobel vertical/horizontal ratio oscillation):
       - Zero std during silence (lips remain closed line -> 0.0 energy).
       - Zero std during head turns/nods (lips remain closed -> 0.0 energy).
       - High std during phonation (syllable opening/closing -> 4.0-9.0 energy).
    3. Zero-mean unit-variance contrast normalization (cancels auto-exposure shifts).
    4. Forehead phase-correlation alignment with subpixel deadband.
    5. Multi-frame hysteresis (2-frame speech entry, 3-frame silence hangover).

    Direct Log-Likelihood Ratios:
       - Empty seat: -4.0 (Hard veto preventing camera jumps to empty chairs).
       - Occupied, silent: -0.5 (Neutral occupancy hold).
       - Occupied, actively speaking: +3.5 (Confirmed speech lock).
    """

    LLR_EMPTY = -4.0
    LLR_QUIET = -0.5
    LLR_SPEAKING = 3.5

    def __init__(
        self,
        model_path: str | Path | None = None,
        hold_seconds: float = 30.0,
        speech_threshold: float = 1.40,
        score_threshold: float = 0.50,
        quiet_threshold: float = 0.70,
        speech_entry_frames: int = 2,
        speech_hangover_frames: int = 3,
        upper_motion_weight: float = 1.20,
        min_face_px: int = 10,
        ema_alpha: float = 0.45,
        input_size: tuple[int, int] = (320, 180),
        pad_margin: float = 0.25,
    ) -> None:
        self.model_path = Path(model_path) if model_path else DEFAULT_YUNET_PATH
        self.hold_seconds = float(hold_seconds)
        self.speech_threshold = float(speech_threshold)
        self.score_threshold = float(score_threshold)
        self.quiet_threshold = float(quiet_threshold)
        self.speech_entry_frames = max(1, int(speech_entry_frames))
        self.speech_hangover_frames = max(1, int(speech_hangover_frames))
        self.upper_motion_weight = float(upper_motion_weight)
        self.min_face_px = int(min_face_px)
        self.ema_alpha = float(np.clip(ema_alpha, 0.05, 0.95))
        self.input_size = (int(input_size[0]), int(input_size[1]))
        self.pad_margin = float(pad_margin)

        self.detector = None
        self.disabled = False
        self.warning = ""
        self.costs = deque(maxlen=10)

        self.seat_last_seen: dict[str, float] = {}
        self.speech_energy: dict[str, float] = {}
        self.speaking_state: dict[str, bool] = {}
        self.occupancy_state: dict[str, bool] = {}
        self.last_llr: dict[str, float] = {}
        self.detected_face_boxes: dict[str, list[int]] = {}
        self.all_faces: list[list[int]] = []
        self.unassigned_faces: list[list[int]] = []
        self.target_poses: dict[str, str] = {}
        self.binding_scores: dict[str, float] = {}
        self.binding_rejected: dict[str, bool] = {}
        self.teaching_targets: dict[str, dict[str, Any]] = {}
        self.roi_kinds: dict[str, str] = {}

        self.prev_aligned_face: dict[str, np.ndarray] = {}
        self.aperture_history: dict[str, deque[float]] = {}
        self.speaking_frames: dict[str, int] = {}
        self.silence_frames: dict[str, int] = {}
        self.teaching_anchors: dict[str, tuple[list[int], np.ndarray, float]] = {}
        self._teaching_bg_sub: dict[str, Any] = {}
        self._teaching_bg_frames: dict[str, int] = {}
        self._teaching_prev_gray: dict[str, np.ndarray] = {}
        self._body_detector: Any = None
        self._body_detector_checked = False
        self.last = -1.0
        self.valid_faces: list[np.ndarray] = []
        self.group_selector = None
        self.group_speaker: dict[str, Any] = {
            "locked_track_id": None,
            "target_box": None,
            "reason": "idle",
            "zoom_action": "none",
            "landmarks_model": "flux_fallback",
            "tracks": [],
        }
        try:
            from confdeck.vision.lipsync import GroupSpeakerSelector
            self.group_selector = GroupSpeakerSelector()
        except Exception:
            self.group_selector = None

        if self.model_path.is_file():
            try:
                if hasattr(cv2, "FaceDetectorYN"):
                    self.detector = cv2.FaceDetectorYN.create(
                        str(self.model_path), "", self.input_size, score_threshold=self.score_threshold, top_k=50
                    )
            except Exception as exc:
                self.warning = f"Face detector init error: {exc}"

    def _detect_faces(self, bgr: np.ndarray) -> tuple[int, np.ndarray | None]:
        if self.detector is not None and bgr is not None and bgr.size > 0:
            h, w = bgr.shape[:2]
            if (w, h) != self.input_size:
                self.detector.setInputSize((w, h))
                self.input_size = (w, h)
            return self.detector.detect(bgr)
        return 0, None

    def reset(self, seat_id: str | None = None) -> None:
        if seat_id is not None:
            self.seat_last_seen.pop(seat_id, None)
            self.speech_energy.pop(seat_id, None)
            self.speaking_state.pop(seat_id, None)
            self.occupancy_state.pop(seat_id, None)
            self.last_llr.pop(seat_id, None)
            self.detected_face_boxes.pop(seat_id, None)
            self.target_poses.pop(seat_id, None)
            self.binding_scores.pop(seat_id, None)
            self.binding_rejected.pop(seat_id, None)
            self.teaching_targets.pop(seat_id, None)
            self.roi_kinds.pop(seat_id, None)
            self.teaching_anchors.pop(seat_id, None)
            self._teaching_bg_sub.pop(seat_id, None)
            self._teaching_bg_frames.pop(seat_id, None)
            self._teaching_prev_gray.pop(seat_id, None)
            self.prev_aligned_face.pop(seat_id, None)
            self.aperture_history.pop(seat_id, None)
            self.speaking_frames.pop(seat_id, None)
            self.silence_frames.pop(seat_id, None)
        else:
            self.seat_last_seen.clear()
            self.speech_energy.clear()
            self.speaking_state.clear()
            self.occupancy_state.clear()
            self.last_llr.clear()
            self.detected_face_boxes.clear()
            self.all_faces.clear()
            self.unassigned_faces.clear()
            self.target_poses.clear()
            self.binding_scores.clear()
            self.binding_rejected.clear()
            self.teaching_targets.clear()
            self.roi_kinds.clear()
            self.teaching_anchors.clear()
            self._teaching_bg_sub.clear()
            self._teaching_bg_frames.clear()
            self._teaching_prev_gray.clear()
            self.prev_aligned_face.clear()
            self.aperture_history.clear()
            self.speaking_frames.clear()
            self.silence_frames.clear()
            self.costs.clear()
            self.last = -1.0
            self.disabled = False
            self.warning = ""
            self.valid_faces.clear()
            if self.group_selector is not None:
                self.group_selector.reset()
            self.group_speaker = {
                "locked_track_id": None,
                "target_box": None,
                "reason": "idle",
                "zoom_action": "none",
                "landmarks_model": (
                    "insightface_2d106det"
                    if (self.group_selector is not None and self.group_selector.scorer.landmarks.available)
                    else "flux_fallback"
                ),
                "tracks": [],
            }

    @staticmethod
    def _normalize_roi(roi: Any, frame_w: int, frame_h: int) -> tuple[float, float, float, float] | None:
        if roi is None:
            return None
        if isinstance(roi, dict):
            if "rect" in roi and isinstance(roi["rect"], (list, tuple)) and len(roi["rect"]) >= 4:
                rx, ry, rw, rh = [float(v) for v in roi["rect"][:4]]
            else:
                rx = float(roi.get("x", 0.0))
                ry = float(roi.get("y", 0.0))
                rw = float(roi.get("w", roi.get("width", 0.0)))
                rh = float(roi.get("h", roi.get("height", 0.0)))
        elif isinstance(roi, (list, tuple)) and len(roi) >= 4:
            rx, ry, rw, rh = float(roi[0]), float(roi[1]), float(roi[2]), float(roi[3])
        else:
            return None

        if rw <= 0.0 or rh <= 0.0:
            return None

        # Support normalized [0..1] ROIs if passed
        if max(rx, ry, rw, rh) <= 1.05:
            rx *= frame_w
            ry *= frame_h
            rw *= frame_w
            rh *= frame_h

        return rx, ry, rw, rh

    @staticmethod
    def _is_valid_face_geometry(face: np.ndarray, frame_w: int, frame_h: int) -> bool:
        """Commercial anthropomorphic face geometry verification.
        
        Rejects non-face artifacts (bags, clothes, furniture, back-of-head) by verifying:
        1. Aspect ratio within natural human face bounds (0.45 to 1.30).
        2. Eye-to-face width ratio (0.18 to 0.68) and minimum inter-ocular span.
        3. Eye horizontal roll angle (tilt < 37 degrees).
        4. Anatomical vertical sequence (eyes strictly above nose, nose above mouth).
        5. Mouth width proportions relative to eye distance.
        6. Landmark containment within bounding box boundary.
        """
        if face is None or len(face) < 4:
            return False
        fx, fy, fw, fh = float(face[0]), float(face[1]), float(face[2]), float(face[3])
        if fw < 10.0 or fh < 10.0 or fw > frame_w or fh > frame_h:
            return False
        aspect = fw / max(1.0, fh)
        if aspect < 0.45 or aspect > 1.30:
            return False

        # If landmarks are not present or not 14+ elements, accept based on bbox
        if len(face) < 14 or not all(np.isfinite(face[4:14])):
            return True

        re_x, re_y = float(face[4]), float(face[5])
        le_x, le_y = float(face[6]), float(face[7])
        n_x, n_y = float(face[8]), float(face[9])
        rm_x, rm_y = float(face[10]), float(face[11])
        lm_x, lm_y = float(face[12]), float(face[13])

        eye_dist = float(np.hypot(le_x - re_x, le_y - re_y))
        mouth_dist = float(np.hypot(lm_x - rm_x, lm_y - rm_y))

        # 1. Anthropometric inter-ocular distance
        if eye_dist < 3.0:
            return False
        eye_ratio = eye_dist / fw
        if eye_ratio < 0.18 or eye_ratio > 0.68:
            return False

        # 2. Eye roll angle (head tilt)
        eye_dx = abs(le_x - re_x)
        eye_dy = abs(le_y - re_y)
        if (eye_dy / max(1e-3, eye_dx)) > 0.75:
            return False

        # 3. Vertical hierarchy: eyes above nose, nose above mouth (tolerant to head pitch & rear angles)
        eye_y = (re_y + le_y) / 2.0
        mouth_y = (rm_y + lm_y) / 2.0
        if n_y < eye_y - 1.0 or mouth_y < n_y - 1.0:
            return False

        # 4. Mouth width proportion relative to eye distance
        if mouth_dist < 1.0:
            return False
        mouth_eye_ratio = mouth_dist / eye_dist
        if mouth_eye_ratio < 0.25 or mouth_eye_ratio > 1.80:
            return False

        # 5. Landmark containment inside bounding box (with 15% tolerance)
        pad_x = fw * 0.15
        pad_y = fh * 0.15
        min_x, max_x = fx - pad_x, fx + fw + pad_x
        min_y, max_y = fy - pad_y, fy + fh + pad_y
        for lx, ly in [(re_x, re_y), (le_x, le_y), (n_x, n_y), (rm_x, rm_y), (lm_x, lm_y)]:
            if not (min_x <= lx <= max_x and min_y <= ly <= max_y):
                return False

        return True

    @staticmethod
    def extract_face_signature(gray_or_bgr: np.ndarray, box: Any) -> list[float] | None:
        """Compute compact illumination-normalized cranial/facial signature (208 dims, unit L2)."""
        if gray_or_bgr is None or box is None or len(box) < 4:
            return None
        gray = gray_or_bgr if gray_or_bgr.ndim == 2 else cv2.cvtColor(gray_or_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        x, y, bw, bh = [int(round(float(v))) for v in box[:4]]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(w, x + bw), min(h, y + bh)
        if (x2 - x1) < 6 or (y2 - y1) < 6:
            return None
        crop = gray[y1:y2, x1:x2]
        small = cv2.resize(crop, (12, 12), interpolation=cv2.INTER_AREA).astype(np.float32)
        flat_i = small.reshape(-1) - float(np.mean(small))
        ni = float(np.linalg.norm(flat_i))
        if ni > 1e-5:
            flat_i = flat_i / ni
        med = cv2.resize(crop, (10, 10), interpolation=cv2.INTER_AREA).astype(np.float32)
        gx = cv2.Sobel(med, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(med, cv2.CV_32F, 0, 1, ksize=3)
        gmag = cv2.magnitude(gx, gy)[1:9, 1:9].reshape(-1)
        gmag = gmag - float(np.mean(gmag))
        ng = float(np.linalg.norm(gmag))
        if ng > 1e-5:
            gmag = gmag / ng
        vec = np.concatenate([flat_i * 0.75, gmag * 0.66])
        nv = float(np.linalg.norm(vec))
        if nv <= 1e-6:
            return None
        vec = vec / nv
        return [round(float(v), 4) for v in vec]

    @staticmethod
    def signature_similarity(sig_a: list[float] | None, sig_b: list[float] | None) -> float:
        if not sig_a or not sig_b or len(sig_a) != len(sig_b):
            return 0.0
        a = np.asarray(sig_a, dtype=np.float32)
        b = np.asarray(sig_b, dtype=np.float32)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom <= 1e-8:
            return 0.0
        return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))

    @staticmethod
    def _roi_kind(roi: Any) -> str:
        if isinstance(roi, dict):
            val = str(roi.get("kind") or "seat").strip().lower()
            if val in ("blackboard", "board", "screen", "presenter", "teacher", "teaching_zone", "teaching"):
                return "teaching"
        return "seat"

    def _get_body_detector(self):
        """Lazy-load the YOLOX person detector on first use."""
        if not self._body_detector_checked:
            self._body_detector_checked = True
            try:
                from confdeck.vision.body import YOLOXPersonDetector
                det = YOLOXPersonDetector()
                if det.available:
                    self._body_detector = det
            except Exception:
                self._body_detector = None
        return self._body_detector

    def _detect_teaching_body(
        self,
        img: np.ndarray,
        roi_rect: tuple[float, float, float, float],
        masks: list[Any],
        seat_id: str,
        now: float,
        quiet: bool,
    ) -> list[int] | None:
        """Detect a teacher's body inside a teaching ROI (front, profile, or back-to-camera).

        Primary: YOLOX person detector (body-level, works from behind and resists wall/board edges).
        Fallback: silhouette-based detection only when YOLOX model is unavailable.
        """
        fh_img, fw_img = img.shape[:2]
        rx, ry, rw, rh = [int(round(v)) for v in roi_rect]
        rx = max(0, min(fw_img - 1, rx))
        ry = max(0, min(fh_img - 1, ry))
        rw = min(fw_img - rx, rw)
        rh = min(fh_img - ry, rh)
        if rw < 14 or rh < 14:
            return None

        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # --- YOLOX body detection (primary) ---
        body_det = self._get_body_detector()
        if body_det is not None:
            bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img
            prev_anchor = self.teaching_anchors.get(seat_id)
            prev_box = prev_anchor[0] if prev_anchor else None
            persons = body_det.detect_persons_in_roi(
                bgr,
                (rx, ry, rw, rh),
                min_overlap=0.25,
                masks=masks,
                prev_box=prev_box,
            )
            if persons:
                bx, by, bw, bh, _conf = persons[0]
                # Focus on upper torso/head-and-shoulders for steady, well-framed PTZ aiming
                head_h = bh if bh <= int(bw * 1.35) else min(bh, max(int(bw * 1.15), int(bh * 0.56)))
                result = [int(bx), int(by), int(bw), max(12, int(head_h))]
                tx, ty, tw, th = result
                cx1, cy1 = max(0, tx - rx), max(0, ty - ry)
                cx2, cy2 = min(rw, cx1 + tw), min(rh, cy1 + th)
                work = gray[ry:ry + rh, rx:rx + rw]
                if cy2 > cy1 and cx2 > cx1:
                    patch = work[cy1:cy2, cx1:cx2]
                    if patch.size >= 64:
                        self.teaching_anchors[seat_id] = (result, patch.copy(), now)
                return result
            # YOLOX found no person — try short motionless template hold, NEVER raw edge blobs
            if seat_id in self.teaching_anchors:
                return self._template_fallback(gray, rx, ry, rw, rh, seat_id, now)
            return None

        # --- Silhouette-based fallback (when YOLOX unavailable) ---
        return self._detect_teaching_body_silhouette(gray, rx, ry, rw, rh, masks, seat_id, now, quiet)

    def _template_fallback(
        self, gray: np.ndarray,
        rx: int, ry: int, rw: int, rh: int,
        seat_id: str, now: float,
    ) -> list[int] | None:
        """Brief (<0.45s) template-match bridge across single-frame YOLOX misses; never freezes on static background."""
        prev_box, anchor_patch, anchor_t = self.teaching_anchors[seat_id]
        ah, aw = anchor_patch.shape[:2]
        if (now - anchor_t) > 0.45 or rh < ah + 2 or rw < aw + 2 or aw < 8 or ah < 8:
            self.teaching_anchors.pop(seat_id, None)
            return None
        if float(np.std(anchor_patch)) < 6.0:
            self.teaching_anchors.pop(seat_id, None)
            return None
        work = gray[ry:ry + rh, rx:rx + rw]
        res = cv2.matchTemplate(work, anchor_patch, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val >= 0.65:
            lx, ly = int(max_loc[0]), int(max_loc[1])
            cand_patch = work[ly:ly + ah, lx:lx + aw]
            if float(np.std(cand_patch)) >= 6.0:
                return [rx + lx, ry + ly, aw, ah]
        return None

    def _detect_teaching_body_silhouette(
        self,
        gray: np.ndarray,
        rx: int, ry: int, rw: int, rh: int,
        masks: list[Any],
        seat_id: str,
        now: float,
        quiet: bool,
    ) -> list[int] | None:
        """Legacy silhouette-based body detection (MOG2 + gradient)."""
        fh_img, fw_img = gray.shape[:2]
        work = gray[ry:ry + rh, rx:rx + rw].copy()
        valid_mask = np.full((rh, rw), 255, dtype=np.uint8)
        roi_med = float(np.median(work))
        for m in masks or []:
            if isinstance(m, (list, tuple)) and len(m) >= 4:
                mx, my, mw, mh = [int(round(float(v))) for v in m[:4]]
                ix1 = max(0, mx - rx)
                iy1 = max(0, my - ry)
                ix2 = min(rw, mx + mw - rx)
                iy2 = min(rh, my + mh - ry)
                if ix2 > ix1 and iy2 > iy1:
                    valid_mask[iy1:iy2, ix1:ix2] = 0
                    work[iy1:iy2, ix1:ix2] = int(roi_med)

        border_vals = np.concatenate([
            work[:min(3, rh), :].reshape(-1),
            work[:, :min(3, rw)].reshape(-1),
            work[:, max(0, rw - 3):].reshape(-1),
        ])
        bg_val = float(np.median(border_vals)) if border_vals.size else roi_med

        gx = cv2.Sobel(work, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(work, cv2.CV_32F, 0, 1, ksize=3)
        grad = cv2.magnitude(gx, gy)
        diff = np.abs(work.astype(np.float32) - bg_val)

        bg_sub = self._teaching_bg_sub.get(seat_id)
        if bg_sub is None and hasattr(cv2, "createBackgroundSubtractorMOG2"):
            bg_sub = cv2.createBackgroundSubtractorMOG2(history=90, varThreshold=22, detectShadows=False)
            self._teaching_bg_sub[seat_id] = bg_sub
        f_count = self._teaching_bg_frames.get(seat_id, 0) + 1
        self._teaching_bg_frames[seat_id] = f_count

        fg_mask = None
        if bg_sub is not None:
            lr = 0.0 if quiet else (0.015 if f_count >= 3 else 0.35)
            raw_fg = bg_sub.apply(work, learningRate=lr)
            if f_count >= 3:
                fg_mask = cv2.bitwise_and(raw_fg, valid_mask)

        motion_mask = np.zeros((rh, rw), dtype=np.uint8)
        prev_gray = self._teaching_prev_gray.get(seat_id)
        if prev_gray is not None and prev_gray.shape == work.shape:
            t_diff = np.abs(work.astype(np.float32) - prev_gray.astype(np.float32))
            motion_mask[t_diff > 16.0] = 255
        self._teaching_prev_gray[seat_id] = work.copy()
        if fg_mask is not None:
            fg_ratio = float(np.count_nonzero(fg_mask)) / max(1.0, float(rw * rh))
            if 0.008 <= fg_ratio <= 0.72:
                motion_mask = cv2.bitwise_or(motion_mask, fg_mask)

        allow_cold_static = (f_count < 4) and (float(np.std(border_vals)) < 20.0 if border_vals.size else False)
        static_body = np.where(((diff > 18.0) | (grad > 22.0)) & (valid_mask > 0), 255, 0).astype(np.uint8)
        if allow_cold_static:
            sil_mask = cv2.bitwise_or(motion_mask, static_body)
        else:
            fg_evidence = cv2.dilate(motion_mask, np.ones((7, 7), dtype=np.uint8)) if np.any(motion_mask) else motion_mask
            sil_mask = cv2.bitwise_or(motion_mask, cv2.bitwise_and(static_body, fg_evidence))

        kernel_c = np.ones((5, 5), dtype=np.uint8)
        kernel_o = np.ones((3, 3), dtype=np.uint8)
        sil_mask = cv2.morphologyEx(sil_mask, cv2.MORPH_CLOSE, kernel_c)
        sil_mask = cv2.morphologyEx(sil_mask, cv2.MORPH_OPEN, kernel_o)
        sil_mask = cv2.bitwise_and(sil_mask, valid_mask)

        min_area = max(75, int(0.012 * rw * rh))
        max_area = int(0.75 * rw * rh)
        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(sil_mask, connectivity=8)

        best_box: list[int] | None = None
        best_score = 0.0
        for lbl in range(1, num_labels):
            bx, by, bw, bh, area = [int(v) for v in stats[lbl]]
            if area < min_area or area > max_area or bw < 10 or bh < 12:
                continue
            if bw >= int(rw * 0.92) and bh >= int(rh * 0.92):
                continue
            if (bh / max(1.0, float(bw))) < 0.55:
                continue
            sub_patch = work[by:by + bh, bx:bx + bw]
            sub_grad = grad[by:by + bh, bx:bx + bw]
            sub_diff = diff[by:by + bh, bx:bx + bw]
            if float(np.std(sub_patch)) < 5.0 and float(np.mean(sub_grad)) < 6.0 and float(np.mean(sub_diff)) < 14.0:
                continue
            comp_score = float(area) * (1.0 + 0.03 * float(np.mean(sub_grad)))
            if comp_score > best_score:
                best_score = comp_score
                head_h = bh if bh <= int(bw * 1.35) else min(bh, max(int(bw * 1.15), int(bh * 0.54)))
                best_box = [rx + bx, ry + by, bw, max(12, head_h)]

        if best_box is None and seat_id in self.teaching_anchors:
            best_box = self._template_fallback(gray, rx, ry, rw, rh, seat_id, now)

        if best_box is not None:
            tx, ty, tw, th = best_box
            cx1, cy1 = max(0, tx - rx), max(0, ty - ry)
            cx2, cy2 = min(rw, cx1 + tw), min(rh, cy1 + th)
            patch = work[cy1:cy2, cx1:cx2]
            if patch.size >= 64:
                self.teaching_anchors[seat_id] = (best_box, patch.copy(), now)
        return best_box

    def _match_face_to_seat(self, face: np.ndarray, roi: tuple[float, float, float, float]) -> bool:
        fx, fy, fw, fh = float(face[0]), float(face[1]), float(face[2]), float(face[3])
        rx, ry, rw, rh = roi

        pad_w = max(12.0, rw * self.pad_margin)
        pad_h = max(12.0, rh * self.pad_margin)

        cx = fx + 0.5 * fw
        cy = fy + 0.5 * fh

        inside_padded = (
            (rx - pad_w) <= cx <= (rx + rw + pad_w)
            and (ry - pad_h) <= cy <= (ry + rh + pad_h)
        )

        overlap_x = max(0.0, min(rx + rw, fx + fw) - max(rx, fx))
        overlap_y = max(0.0, min(ry + rh, fy + fh) - max(ry, fy))
        overlap_area = overlap_x * overlap_y
        face_area = max(1e-6, fw * fh)

        return bool(inside_padded or ((overlap_area / face_area) > 0.10))

    def _extract_normalized_face(
        self, gray: np.ndarray, face: np.ndarray
    ) -> tuple[np.ndarray | None, tuple[int, int], np.ndarray | None]:
        h, w = gray.shape[:2]
        fx, fy, fw, fh = float(face[0]), float(face[1]), float(face[2]), float(face[3])

        if fw < self.min_face_px or fh < self.min_face_px:
            return None, (16, 26), None

        patch: np.ndarray | None = None
        mouth_crop: np.ndarray | None = None
        mouth_center_32x40: tuple[int, int] = (16, 26)

        # Check if YuNet 5-point facial landmarks are present
        if len(face) >= 14 and all(np.isfinite(face[4:14])):
            re = np.array([float(face[4]), float(face[5])], dtype=np.float32)
            le = np.array([float(face[6]), float(face[7])], dtype=np.float32)
            rm = np.array([float(face[10]), float(face[11])], dtype=np.float32)
            lm = np.array([float(face[12]), float(face[13])], dtype=np.float32)

            eye_dist = float(np.linalg.norm(le - re))
            if eye_dist >= 4.0 and (rm[0] > 0 or lm[0] > 0):
                eye_c = (re + le) / 2.0
                mouth_c = (rm + lm) / 2.0

                # 1. RIGID CRANIAL-ANCHORED symmetric face crop
                # Anchored to the eyes (cranial bone) rather than mouth so jaw-drop
                # during speech does NOT shift the crop upwards or trigger false head motion!
                face_cx = eye_c[0]
                face_cy = eye_c[1] + eye_dist * 0.40  # fixed anatomical skull offset
                half_w = eye_dist * 1.50
                half_h = eye_dist * 1.875  # 40/32 standard face aspect ratio

                x1 = int(round(max(0.0, face_cx - half_w)))
                x2 = int(round(min(float(w), face_cx + half_w)))
                y1 = int(round(max(0.0, face_cy - half_h)))
                y2 = int(round(min(float(h), face_cy + half_h)))

                if (x2 - x1) >= 4 and (y2 - y1) >= 4:
                    raw_crop = gray[y1:y2, x1:x2]
                    if raw_crop.size > 0:
                        patch = raw_crop
                        u = (mouth_c[0] - float(x1)) / max(1.0, float(x2 - x1))
                        v = (mouth_c[1] - float(y1)) / max(1.0, float(y2 - y1))
                        mx_32 = int(np.clip(round(u * 32.0), 6, 25))
                        my_40 = int(np.clip(round(v * 40.0), 18, 33))
                        mouth_center_32x40 = (mx_32, my_40)

                # 2. Extract scale-adaptive mouth aperture crop directly from image
                mw = max(4.0, eye_dist * 0.95)
                mh = max(3.0, mw * 0.55)
                mx_start = int(max(0, mouth_c[0] - mw * 0.55))
                mx_end = int(min(w, mouth_c[0] + mw * 0.55))
                my_start = int(max(0, mouth_c[1] - mh * 0.45))
                my_end = int(min(h, mouth_c[1] + mh * 0.55))
                if (mx_end - mx_start) >= 2 and (my_end - my_start) >= 2:
                    mouth_raw = gray[my_start:my_end, mx_start:mx_end]
                    if mouth_raw.size > 0:
                        mouth_crop = cv2.resize(mouth_raw, (28, 16), interpolation=cv2.INTER_AREA)

        # Fallback to bounding box crop if landmarks unavailable or degenerate
        if patch is None or patch.size == 0:
            pad_x = int(round(fw * 0.20))
            pad_y = int(round(fh * 0.20))
            x1 = max(0, int(np.floor(fx - pad_x)))
            y1 = max(0, int(np.floor(fy - pad_y)))
            x2 = min(w, int(np.ceil(fx + fw + pad_x)))
            y2 = min(h, int(np.ceil(fy + fh + pad_y)))
            if (x2 - x1) < 4 or (y2 - y1) < 4:
                return None, (16, 26), None
            patch = gray[y1:y2, x1:x2]
            if patch.size == 0:
                return None, (16, 26), None
            my1, my2 = int(fy + fh * 0.65), int(fy + fh * 0.95)
            mx1, mx2 = int(fx + fw * 0.25), int(fx + fw * 0.75)
            if my2 > my1 and mx2 > mx1:
                mouth_crop = cv2.resize(gray[my1:my2, mx1:mx2], (28, 16), interpolation=cv2.INTER_AREA)

        # Standardize face patch to 32x40
        resized = cv2.resize(patch, (32, 40), interpolation=cv2.INTER_AREA).astype(np.float32)

        # Zero-mean unit-variance contrast normalization
        mean = float(np.mean(resized))
        std = float(np.std(resized))
        norm = (resized - mean) / max(std, 8.0)

        return norm, mouth_center_32x40, mouth_crop

    def _compute_oral_motion(
        self,
        curr_norm: np.ndarray,
        prev_norm: np.ndarray | None,
        mouth_center: tuple[int, int],
        mouth_crop: np.ndarray | None,
        seat_id: str,
    ) -> float:
        # 1. Lip Aperture Dynamics (Sobel vertical/horizontal ratio oscillation)
        # Closed mouth = constant horizontal line -> 0.0 aperture std across window.
        # Speaking mouth = alternating open aperture / teeth / lips -> high std.
        ap_score = 0.0
        if mouth_crop is not None and mouth_crop.size > 0:
            gy = cv2.Sobel(mouth_crop, cv2.CV_32F, 0, 1, ksize=3)
            gx = cv2.Sobel(mouth_crop, cv2.CV_32F, 1, 0, ksize=3)
            mean_gy = float(np.mean(np.abs(gy)))
            mean_gx = float(np.mean(np.abs(gx)))
            ap = (mean_gy + 1.0) / (mean_gx + 1.0)
            history = self.aperture_history.setdefault(seat_id, deque(maxlen=4))
            history.append(ap)
            if len(history) >= 3:
                ap_std = float(np.std(list(history)))
                ap_score = max(0.0, (ap_std - 0.05)) * 5.0

        if prev_norm is None or prev_norm.shape != curr_norm.shape:
            return float(ap_score)

        # 2. Upper face rigid skull alignment (rows 2:18, height 16)
        upper_curr = curr_norm[2:18, :]
        upper_prev = prev_norm[2:18, :]

        shift_xy, _ = cv2.phaseCorrelate(upper_prev, upper_curr)
        dx, dy = float(shift_xy[0]), float(shift_xy[1])
        head_shift_mag = float(np.hypot(dx, dy))

        # Deadband subpixel interpolation jitter; clamp excessive displacement spikes
        if head_shift_mag < 0.35 or head_shift_mag > 6.0:
            dx, dy = 0.0, 0.0

        if dx == 0.0 and dy == 0.0:
            aligned_prev = prev_norm
        else:
            affine_mat = np.float32([[1.0, 0.0, dx], [0.0, 1.0, dy]])
            aligned_prev = cv2.warpAffine(
                prev_norm,
                affine_mat,
                (32, 40),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )

        diff_map = np.abs(curr_norm - aligned_prev)

        # Upper-face residual acts as baseline for global lighting/exposure changes
        upper_diff = float(np.mean(diff_map[2:16, 6:26]))

        # Focused mouth window (height 6px, width 12px) strictly centered on lip plane
        mx, my = mouth_center
        mx1 = max(4, min(20, mx - 6))
        mx2 = min(28, mx1 + 12)
        my1 = max(18, min(28, my - 3))
        my2 = min(34, my1 + 6)

        mouth_diff = float(np.mean(diff_map[my1:my2, mx1:mx2]))
        diff_score = max(0.0, mouth_diff - self.upper_motion_weight * upper_diff) * 10.0

        # Damping during rigid head movement (> 1.4px)
        if head_shift_mag > 1.4:
            penalty = max(0.20, 1.0 - 0.30 * (head_shift_mag - 1.4))
            diff_score *= penalty
            ap_score *= penalty

        total_oral = diff_score + ap_score
        return float(total_oral)

    def process(
        self,
        frame_pixels: Any,
        rois: dict[str, Any],
        now: float,
        blocked: bool = False,
        quiet: bool = False,
        hi_res_frame: np.ndarray | None = None,
        mic_active: bool | None = None,
    ) -> dict[str, float]:
        start = time.perf_counter()
        if self.disabled:
            return {}
        self.last = now

        # Continue through face detection even with no ROIs so Camera 128 tracking and live overlays get face boxes.
        u8 = np.asarray(frame_pixels, dtype=np.uint8)
        if u8.shape[:2] != (180, 320) and u8.shape[:2] != (self.input_size[1], self.input_size[0]):
            raise ValueError(f"Expected frame shape compatible with {self.input_size}, got {u8.shape}")

        has_teaching = any(self._roi_kind(r) == "teaching" for r in rois.values())
        if blocked or (self.detector is None and not has_teaching):
            self.prev_aligned_face.clear()
            self.aperture_history.clear()
            return {s: (0.0 if blocked else 1.0) for s in rois}

        bgr = cv2.cvtColor(u8, cv2.COLOR_GRAY2BGR) if u8.ndim == 2 else u8
        gray = u8 if u8.ndim == 2 else cv2.cvtColor(u8, cv2.COLOR_BGR2GRAY)
        fh_img, fw_img = gray.shape[:2]

        ret, faces = self._detect_faces(bgr)

        detected_faces: dict[str, np.ndarray] = {}
        valid_faces: list[np.ndarray] = []
        assigned_face_indices: set[int] = set()
        self.target_poses = {}
        self.binding_scores = {}
        self.binding_rejected = {}
        self.teaching_targets = {}
        self.roi_kinds = {s: self._roi_kind(r) for s, r in rois.items()}

        if faces is not None and len(faces) > 0:
            for idx, f in enumerate(faces):
                conf = float(f[14]) if len(f) >= 15 else 1.0
                if conf < self.score_threshold:
                    continue
                if not self._is_valid_face_geometry(f, fw_img, fh_img):
                    continue
                valid_faces.append(f)

        for s, r in rois.items():
            norm_roi = self._normalize_roi(r, fw_img, fh_img)
            if norm_roi is None:
                continue
            expected_sig = r.get("face_signature") if isinstance(r, dict) else None
            sig_thr = float(r.get("signature_threshold", 0.45)) if isinstance(r, dict) else 0.45

            best_face: np.ndarray | None = None
            best_idx: int = -1
            best_score_rank: float = -1.0
            max_sim: float = -1.0
            saw_candidate = False

            for idx, f in enumerate(valid_faces):
                if not self._match_face_to_seat(f, norm_roi):
                    continue
                saw_candidate = True
                conf = float(f[14]) if len(f) >= 15 else 1.0
                if expected_sig:
                    sig = self.extract_face_signature(gray, f[:4])
                    sim = self.signature_similarity(sig, expected_sig)
                    max_sim = max(max_sim, sim)
                    if sim < sig_thr:
                        continue
                    rank = conf * 0.4 + sim * 0.6
                else:
                    rank = conf
                if rank > best_score_rank:
                    best_score_rank = rank
                    best_face = f
                    best_idx = idx

            if expected_sig and saw_candidate:
                self.binding_scores[s] = round(max(0.0, max_sim), 3)
                self.binding_rejected[s] = best_face is None

            if best_face is not None:
                detected_faces[s] = best_face
                assigned_face_indices.add(best_idx)
                self.seat_last_seen[s] = now
                self.target_poses[s] = "front"
                if self.roi_kinds.get(s) == "teaching":
                    bx, by, bw, bh = [int(round(float(v))) for v in best_face[:4]]
                    self.teaching_targets[s] = {"seat": s, "box": [bx, by, bw, bh], "pose": "front", "kind": "teaching"}
                    tx1, ty1 = max(0, bx), max(0, by)
                    tx2, ty2 = min(fw_img, tx1 + max(8, bw)), min(fh_img, ty1 + max(8, bh))
                    if tx2 > tx1 and ty2 > ty1:
                        self.teaching_anchors[s] = ([bx, by, bw, bh], gray[ty1:ty2, tx1:tx2].copy(), now)

        self.detected_face_boxes = {
            s: [int(round(float(f[0]))), int(round(float(f[1]))), int(round(float(f[2]))), int(round(float(f[3])))]
            for s, f in detected_faces.items()
        }
        self.all_faces = [
            [int(round(float(f[0]))), int(round(float(f[1]))), int(round(float(f[2]))), int(round(float(f[3])))]
            for f in valid_faces
        ]
        self.valid_faces = list(valid_faces)
        self.unassigned_faces = [
            [int(round(float(f[0]))), int(round(float(f[1]))), int(round(float(f[2]))), int(round(float(f[3])))]
            for idx, f in enumerate(valid_faces)
            if idx not in assigned_face_indices
        ]
        if self.group_selector is not None:
            self.group_speaker = self.group_selector.update(
                valid_faces,
                gray,
                now,
                hi_res_gray=hi_res_frame,
                quiet=quiet,
                mic_active=mic_active,
            )

        # Mode 1: Inside mapped teaching/blackboard zones:
        # Use fast, pixel-accurate YuNet face detection whenever the teacher's face is visible;
        # only invoke YOLOX body detection when no face is visible (turned away / profile / distant).
        for s, r in rois.items():
            if self.roi_kinds.get(s) != "teaching":
                continue
            if s in detected_faces:
                continue
            norm_roi = self._normalize_roi(r, fw_img, fh_img)
            if norm_roi is None:
                continue
            masks = r.get("masks", []) if isinstance(r, dict) else []
            body_box = self._detect_teaching_body(bgr, norm_roi, masks, s, now, quiet)
            if body_box is not None:
                pose = "back_or_body"
                self.detected_face_boxes[s] = body_box
                self.target_poses[s] = pose
                self.teaching_targets[s] = {"seat": s, "box": body_box, "pose": pose, "kind": "teaching"}
                self.seat_last_seen[s] = now
                if body_box not in self.all_faces:
                    self.all_faces.append(body_box)

        scores: dict[str, float] = {}
        llrs: dict[str, float] = {}

        for s in rois:
            is_present = (now - self.seat_last_seen.get(s, -100.0)) <= self.hold_seconds
            if self.binding_rejected.get(s):
                is_present = False
            self.occupancy_state[s] = is_present

            if not is_present:
                self.speaking_state[s] = False
                self.speech_energy[s] = 0.0
                self.speaking_frames[s] = 0
                self.silence_frames[s] = 0
                scores[s] = 0.0
                llrs[s] = self.LLR_EMPTY  # Hard veto for empty seat
                self.prev_aligned_face.pop(s, None)
                self.aperture_history.pop(s, None)
                continue

            if quiet:
                self.speaking_state[s] = False
                self.speaking_frames[s] = 0
                self.silence_frames[s] = self.speech_hangover_frames
                scores[s] = round(float(self.speech_energy.get(s, 0.0)), 2)
                llrs[s] = self.LLR_QUIET
                continue

            if s in detected_faces:
                f = detected_faces[s]
                curr_norm, mouth_center, mouth_crop = self._extract_normalized_face(gray, f)
                if curr_norm is not None:
                    prev_norm = self.prev_aligned_face.get(s)
                    oral_motion = self._compute_oral_motion(
                        curr_norm, prev_norm, mouth_center, mouth_crop, s
                    )
                    self.prev_aligned_face[s] = curr_norm
                else:
                    oral_motion = 0.0

                # Fuse 106-pt neural lip-sync energy for the matching group track when available
                if self.group_selector is not None and self.group_selector.scorer.landmarks.available:
                    f_cx = float(f[0]) + float(f[2]) * 0.5
                    f_cy = float(f[1]) + float(f[3]) * 0.5
                    for tr in self.group_speaker.get("tracks") or []:
                        tb = tr.get("box") or []
                        if len(tb) >= 4:
                            t_cx = float(tb[0]) + float(tb[2]) * 0.5
                            t_cy = float(tb[1]) + float(tb[3]) * 0.5
                            if abs(t_cx - f_cx) <= max(12.0, float(f[2]) * 0.5) and abs(t_cy - f_cy) <= max(12.0, float(f[3]) * 0.5):
                                oral_motion = max(oral_motion * 0.35, float(tr.get("energy", 0.0)))
                                break

                prev_energy = self.speech_energy.get(s, 0.0)
                energy = (1.0 - self.ema_alpha) * prev_energy + self.ema_alpha * oral_motion
            else:
                self.prev_aligned_face.pop(s, None)
                self.aperture_history.pop(s, None)
                energy = self.speech_energy.get(s, 0.0) * 0.70

            self.speech_energy[s] = energy
            scores[s] = round(energy, 2)

            was_speaking = self.speaking_state.get(s, False)
            if not was_speaking:
                if energy >= self.speech_threshold:
                    self.speaking_frames[s] = self.speaking_frames.get(s, 0) + 1
                    if self.speaking_frames[s] >= self.speech_entry_frames:
                        self.speaking_state[s] = True
                        self.silence_frames[s] = 0
                    else:
                        self.speaking_state[s] = False
                else:
                    self.speaking_frames[s] = 0
                    self.speaking_state[s] = False
            else:
                if energy < self.quiet_threshold:
                    self.silence_frames[s] = self.silence_frames.get(s, 0) + 1
                    if self.silence_frames[s] >= self.speech_hangover_frames:
                        self.speaking_state[s] = False
                        self.speaking_frames[s] = 0
                    else:
                        self.speaking_state[s] = True
                else:
                    self.silence_frames[s] = 0
                    self.speaking_state[s] = True

            llrs[s] = self.LLR_SPEAKING if self.speaking_state[s] else self.LLR_QUIET

        self.last_llr = llrs
        self.costs.append(time.perf_counter() - start)
        lmk_active = bool(self.group_selector is not None and self.group_selector.scorer.landmarks.available)
        budget_s = 0.150 if (lmk_active or (self._body_detector is not None and getattr(self._body_detector, "backend", "") == "opencv_dnn")) else 0.045
        if len(self.costs) == 10 and (sum(self.costs) / 10) > budget_s:
            self.disabled = True
            self.warning = f"Vision disabled: average frame processing exceeded {int(budget_s * 1000)} ms"
            return {}

        return scores

    def evaluate(
        self,
        gray_or_bgr: np.ndarray,
        seat_rois: dict[str, Any],
        now: float | None = None,
        quiet: bool = False,
        hi_res_frame: np.ndarray | None = None,
        mic_active: bool | None = None,
    ) -> dict[str, Any]:
        """Convenience evaluation API returning comprehensive detector dictionary."""
        ts = time.monotonic() if now is None else float(now)
        motion_scores = self.process(
            gray_or_bgr,
            seat_rois,
            ts,
            blocked=False,
            quiet=quiet,
            hi_res_frame=hi_res_frame,
            mic_active=mic_active,
        )
        return {
            "scores": dict(self.last_llr),
            "motion": motion_scores,
            "occupancy": dict(self.occupancy_state),
            "speaking": dict(self.speaking_state),
            "faces": dict(self.detected_face_boxes),
            "all_faces": list(self.all_faces),
            "unassigned_faces": list(self.unassigned_faces),
            "poses": dict(self.target_poses),
            "binding_scores": dict(self.binding_scores),
            "binding_rejected": dict(self.binding_rejected),
            "teaching_targets": dict(self.teaching_targets),
            "group_speaker": dict(self.group_speaker),
        }
