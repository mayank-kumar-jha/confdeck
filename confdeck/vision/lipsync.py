"""Commercial-grade 106-point neural lip landmark & group active-speaker selector.

Built for CPU-only edge hardware (e.g. Intel Core i5-12400, 8 GB RAM, no GPU):
- Primary lip model: InsightFace `2d106det.onnx` (5.0 MB ONNX, ~4-8 ms/face CPU)
  predicting 106 sub-pixel 2D facial landmarks including 20 inner/outer lip
  points via `onnxruntime` (zero PyTorch/MediaPipe runtime dependency).
- Multi-person "Super-Seat" group disambiguation (`GroupSpeakerSelector`):
  1. Tracks every face in the group preset with persistent `track_id`.
  2. Crops 192x192 aligned patches from the sharpest available frame (`hi_res_gray`
     when present, falling back to the 320x180 reference frame).
  3. Measures Inner Mouth Aspect Ratio (inner_mar), Outer MAR, and Jaw Drop across
     a rolling 14-frame window (~0.8 s) with 2-8 Hz syllable-cadence filtering
     so nods, smiles, turns, and lighting shifts do NOT trigger false locks.
  4. Locks onto the dominant speaking person and emits bounded PTZ zoom commands
     until portrait framing is reached, holding until a new seat is locked.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np

DEFAULT_2D106_PATH = Path(__file__).resolve().parents[2] / "artifacts" / "vision-model" / "2d106det.onnx"

# InsightFace 106-point -> Dlib 68-point lip indices
# Outer lip corners: 52 (left), 61 (right); Outer top/bottom center: 71, 53
# Inner lip corners: 65 (left), 69 (right); Inner top: 66, 62, 70; Inner bottom: 54, 60, 57
MOUTH_ALL_IDX = (52, 64, 63, 71, 67, 68, 61, 58, 59, 53, 56, 55, 65, 66, 62, 70, 69, 57, 60, 54)
INNER_TOP_IDX = (66, 62, 70)
INNER_BOT_IDX = (54, 60, 57)
OUTER_TOP_IDX = (64, 71, 68)
OUTER_BOT_IDX = (56, 53, 58)


def _clamp_rect(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> tuple[int, int, int, int] | None:
    cx1 = max(0, min(w - 1, int(x1)))
    cy1 = max(0, min(h - 1, int(y1)))
    cx2 = max(cx1, min(w, int(x2)))
    cy2 = max(cy1, min(h, int(y2)))
    if (cx2 - cx1) < 4 or (cy2 - cy1) < 3:
        return None
    return cx1, cy1, cx2, cy2


class LipLandmark106Model:
    """ONNX Runtime wrapper for InsightFace `2d106det.onnx` neural lip landmarking."""

    def __init__(self, model_path: str | Path | None = None, num_threads: int = 2) -> None:
        self.model_path = Path(model_path) if model_path else DEFAULT_2D106_PATH
        self.session = None
        self.input_name = "data"
        self.input_size = 192
        self.warning = ""

        if not self.model_path.is_file():
            self.warning = f"106-point landmark model missing at {self.model_path}"
            return
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(1, int(num_threads))
            opts.inter_op_num_threads = 1
            self.session = ort.InferenceSession(
                str(self.model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            inp = self.session.get_inputs()[0]
            self.input_name = inp.name
            if len(inp.shape) >= 4 and isinstance(inp.shape[2], int):
                self.input_size = int(inp.shape[2])
        except Exception as exc:
            self.session = None
            self.warning = f"Failed to load 2d106det.onnx: {exc}"

    @property
    def available(self) -> bool:
        return self.session is not None

    def predict(
        self,
        frame: np.ndarray,
        face_box: list[float] | tuple[float, ...] | np.ndarray,
        scale_xy: tuple[float, float] = (1.0, 1.0),
    ) -> dict[str, Any] | None:
        """Predict 106 landmarks + exact lip metrics for one face bounding box.

        `face_box` is `[x, y, w, h]` scaled by `scale_xy` into `frame` coordinates.
        Returns lip points mapped back into reference coordinates (`1/scale_xy`).
        """
        if self.session is None or frame is None or frame.size == 0 or len(face_box) < 4:
            return None

        bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame
        sx, sy = float(scale_xy[0]), float(scale_xy[1])
        fx, fy, fw, fh = float(face_box[0]) * sx, float(face_box[1]) * sy, float(face_box[2]) * sx, float(face_box[3]) * sy
        if fw < 6.0 or fh < 6.0:
            return None

        cx = fx + fw * 0.5
        cy = fy + fh * 0.5
        size = float(self.input_size)
        scale = size / (max(fw, fh) * 1.5)
        if not np.isfinite(scale) or scale <= 0.0:
            return None

        tx = size * 0.5 - cx * scale
        ty = size * 0.5 - cy * scale
        mat = np.float32([[scale, 0.0, tx], [0.0, scale, ty]])
        aligned = cv2.warpAffine(bgr, mat, (self.input_size, self.input_size), borderValue=0.0)

        # Graph embeds Sub/Mul normalization nodes -> pass raw 0..255 RGB float tensor
        blob = cv2.dnn.blobFromImage(aligned, 1.0, (self.input_size, self.input_size), (0.0, 0.0, 0.0), swapRB=True)
        pred = self.session.run(None, {self.input_name: blob})[0][0].reshape(-1, 2)
        pts_crop = (pred + 1.0) * (size * 0.5)

        # Map back to frame coords, then to reference 320x180 coords
        pts_frame = (pts_crop - (size * 0.5)) / scale + np.array([cx, cy], dtype=np.float32)
        pts_ref = pts_frame / np.array([max(1e-6, sx), max(1e-6, sy)], dtype=np.float32)

        # Lip geometry ratios in scale-invariant crop space
        inner_w = max(4.0, float(np.linalg.norm(pts_crop[69] - pts_crop[65])))
        outer_w = max(5.0, float(np.linalg.norm(pts_crop[61] - pts_crop[52])))
        inner_h = float(
            np.mean([np.linalg.norm(pts_crop[t] - pts_crop[b]) for t, b in zip(INNER_TOP_IDX, INNER_BOT_IDX)])
        )
        outer_h = float(
            np.mean([np.linalg.norm(pts_crop[t] - pts_crop[b]) for t, b in zip(OUTER_TOP_IDX, OUTER_BOT_IDX)])
        )
        inner_mar = inner_h / inner_w
        outer_mar = outer_h / outer_w

        # Nose tip (index 86) to bottom lip (53) normalized by mouth width captures jaw drop
        jaw_span = float(np.linalg.norm(pts_crop[53] - pts_crop[86])) / outer_w

        mouth_pts = pts_ref[list(MOUTH_ALL_IDX)]
        mx1, my1 = np.min(mouth_pts, axis=0)
        mx2, my2 = np.max(mouth_pts, axis=0)
        mouth_box = [
            int(round(max(0.0, mx1 - 2.0))),
            int(round(max(0.0, my1 - 2.0))),
            int(round(max(4.0, mx2 - mx1 + 4.0))),
            int(round(max(3.0, my2 - my1 + 4.0))),
        ]

        return {
            "landmarks_ref": pts_ref,
            "mouth_points_ref": np.round(mouth_pts, 1).tolist(),
            "mouth_box": mouth_box,
            "inner_mar": float(inner_mar),
            "outer_mar": float(outer_mar),
            "jaw_span": float(jaw_span),
        }


@dataclass
class TrackedFaceState:
    """Rolling neural lip landmark & aperture history for one tracked person."""

    track_id: int
    box: list[float]  # [x, y, w, h] in 320x180 reference coords
    last_seen: float
    inner_mar_history: deque[float] = field(default_factory=lambda: deque(maxlen=14))
    outer_mar_history: deque[float] = field(default_factory=lambda: deque(maxlen=14))
    jaw_history: deque[float] = field(default_factory=lambda: deque(maxlen=14))
    mouth_history: deque[np.ndarray] = field(default_factory=lambda: deque(maxlen=14))
    forehead_history: deque[np.ndarray] = field(default_factory=lambda: deque(maxlen=14))
    aperture_history: deque[float] = field(default_factory=lambda: deque(maxlen=14))
    timestamps: deque[float] = field(default_factory=lambda: deque(maxlen=14))
    ema_score: float = 0.0
    speech_prob: float = 0.0
    inner_mar: float = 0.0
    speaking: bool = False
    speak_streak: int = 0
    silence_streak: int = 0
    mouth_rect_ref: list[int] | None = None
    mouth_points_ref: list[list[float]] | None = None


class SpatiotemporalLipScorer:
    """Scores speech activity primarily from 106-pt neural MAR cadence (+ flux fallback)."""

    def __init__(self, landmark_model_path: str | Path | None = None) -> None:
        self.landmarks = LipLandmark106Model(landmark_model_path)

    @staticmethod
    def _zero_crossings(values: np.ndarray) -> int:
        if values.size < 3:
            return 0
        centered = values - np.mean(values)
        scale = max(float(np.std(centered)) * 0.22, 0.008)
        signs = np.where(np.abs(centered) > scale, np.sign(centered), 0.0)
        nz = signs[signs != 0.0]
        if nz.size < 2:
            return 0
        return int(np.sum(nz[1:] * nz[:-1] < 0))

    @staticmethod
    def _extract_fallback_crops(
        face: np.ndarray,
        ref_gray: np.ndarray,
        hi_res_gray: np.ndarray | None = None,
    ) -> tuple[np.ndarray | None, np.ndarray | None, list[int] | None]:
        src = hi_res_gray if (hi_res_gray is not None and hi_res_gray.size > 0) else ref_gray
        if src is None or src.size == 0:
            return None, None, None
        src_gray = src if src.ndim == 2 else cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
        sh, sw = src_gray.shape[:2]
        rh, rw = ref_gray.shape[:2]
        sx, sy = float(sw) / max(1.0, float(rw)), float(sh) / max(1.0, float(rh))
        fx, fy, fw, fh = [float(v) for v in face[:4]]

        mx1, mx2 = fx + fw * 0.22, fx + fw * 0.78
        my1, my2 = fy + fh * 0.62, fy + fh * 0.96
        m_rect = _clamp_rect(int(mx1 * sx), int(my1 * sy), int(mx2 * sx), int(my2 * sy), sw, sh)
        if m_rect is None:
            return None, None, None
        mouth_raw = src_gray[m_rect[1]:m_rect[3], m_rect[0]:m_rect[2]]
        mouth_crop = cv2.resize(mouth_raw, (48, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
        mouth_norm = (mouth_crop - float(np.mean(mouth_crop))) / max(float(np.std(mouth_crop)), 7.5)

        forehead_norm = None
        f_rect = _clamp_rect(int((fx + fw * 0.22) * sx), int((fy + fh * 0.10) * sy), int((fx + fw * 0.78) * sx), int((fy + fh * 0.34) * sy), sw, sh)
        if f_rect is not None:
            f_raw = src_gray[f_rect[1]:f_rect[3], f_rect[0]:f_rect[2]]
            f_crop = cv2.resize(f_raw, (32, 12), interpolation=cv2.INTER_AREA).astype(np.float32)
            forehead_norm = (f_crop - float(np.mean(f_crop))) / max(float(np.std(f_crop)), 7.5)

        mouth_ref = [int(round(max(0.0, mx1))), int(round(max(0.0, my1))), int(round(max(1.0, mx2 - mx1))), int(round(max(1.0, my2 - my1)))]
        return mouth_norm, forehead_norm, mouth_ref

    def update_and_score(
        self,
        state: TrackedFaceState,
        face: np.ndarray,
        ref_gray: np.ndarray,
        now: float,
        hi_res_frame: np.ndarray | None = None,
    ) -> tuple[float, float]:
        """Extract 106 landmarks (when available) + score temporal speech cadence."""
        src = hi_res_frame if (hi_res_frame is not None and hi_res_frame.size > 0) else ref_gray
        sh, sw = src.shape[:2]
        rh, rw = ref_gray.shape[:2]
        scale_xy = (float(sw) / max(1.0, float(rw)), float(sh) / max(1.0, float(rh)))

        lmk = self.landmarks.predict(src, face, scale_xy=scale_xy) if self.landmarks.available else None
        state.timestamps.append(now)

        if lmk is not None:
            state.inner_mar = lmk["inner_mar"]
            state.mouth_rect_ref = lmk["mouth_box"]
            state.mouth_points_ref = lmk["mouth_points_ref"]
            state.inner_mar_history.append(lmk["inner_mar"])
            state.outer_mar_history.append(lmk["outer_mar"])
            state.jaw_history.append(lmk["jaw_span"])

            # Instantaneous neural lip velocity (d(inner_mar) & d(outer_mar))
            if len(state.inner_mar_history) >= 2:
                d_in = abs(state.inner_mar_history[-1] - state.inner_mar_history[-2])
                d_out = abs(state.outer_mar_history[-1] - state.outer_mar_history[-2])
                d_jaw = abs(state.jaw_history[-1] - state.jaw_history[-2])
                inst_vel = d_in * 14.0 + d_out * 7.0 + d_jaw * 5.0
            else:
                inst_vel = 0.0

            # Rolling syllable rhythm & excursion over temporal window
            in_arr = np.asarray(state.inner_mar_history, dtype=np.float32)
            out_arr = np.asarray(state.outer_mar_history, dtype=np.float32)
            osc_score = 0.0
            if in_arr.size >= 3:
                in_std = float(np.std(in_arr))
                out_std = float(np.std(out_arr))
                in_ptp = float(np.ptp(in_arr))
                crossings = max(self._zero_crossings(in_arr), self._zero_crossings(out_arr))
                span_s = max(0.15, float(state.timestamps[-1] - state.timestamps[0])) if len(state.timestamps) >= 2 else 0.3
                cycle_hz = (crossings * 0.5) / span_s
                rhythm_mult = 1.30 if (1.2 <= cycle_hz <= 9.0) else (0.45 if crossings == 0 and in_arr.size >= 6 else 0.9)
                osc_score = (max(0.0, in_std - 0.012) * 32.0 + max(0.0, out_std - 0.015) * 16.0 + max(0.0, in_ptp - 0.04) * 8.0) * rhythm_mult

            # Baseline openness bonus only when accompanied by lip motion (ignores open-mouth yawns)
            active_open = 0.55 if (lmk["inner_mar"] > 0.09 and (inst_vel > 0.35 or osc_score > 0.45)) else 0.0
            raw_energy = inst_vel * 0.65 + osc_score + active_open
        else:
            # Fallback crop-flux path when 2d106det.onnx is unavailable
            m_crop, f_crop, m_rect = self._extract_fallback_crops(face, ref_gray, hi_res_frame)
            state.mouth_rect_ref = m_rect
            if m_crop is not None:
                state.mouth_history.append(m_crop)
            if f_crop is not None:
                state.forehead_history.append(f_crop)
            raw_energy = self._fallback_score(state)

        state.ema_score = 0.55 * state.ema_score + 0.45 * float(raw_energy)
        prob = float(1.0 / (1.0 + np.exp(-2.3 * (state.ema_score - 1.20))))
        state.speech_prob = float(np.clip(prob, 0.0, 1.0))
        return float(state.ema_score), float(state.speech_prob)

    def _fallback_score(self, state: TrackedFaceState) -> float:
        if len(state.mouth_history) < 2:
            return 0.0
        curr_m, prev_m = state.mouth_history[-1], state.mouth_history[-2]
        forehead_diff = 0.0
        if len(state.forehead_history) >= 2:
            forehead_diff = float(np.mean(np.abs(state.forehead_history[-1] - state.forehead_history[-2])))
        inner_diff = float(np.mean(np.abs(curr_m[5:19, 8:40] - prev_m[5:19, 8:40])))
        flux = max(0.0, inner_diff - 1.15 * forehead_diff) * 8.0
        gy = cv2.Sobel(curr_m, cv2.CV_32F, 0, 1, ksize=3)
        gx = cv2.Sobel(curr_m, cv2.CV_32F, 1, 0, ksize=3)
        ap = float(np.mean(np.abs(gy[4:20, 8:40])) + 0.5) / float(np.mean(np.abs(gx[4:20, 8:40])) + 0.5)
        state.aperture_history.append(ap)
        ap_std = float(np.std(list(state.aperture_history))) if len(state.aperture_history) >= 3 else 0.0
        return flux * 0.7 + max(0.0, ap_std - 0.03) * 14.0


class GroupSpeakerSelector:
    """Identifies who is speaking inside a multi-person super-seat and guides PTZ zoom."""

    def __init__(
        self,
        *,
        entry_prob: float = 0.55,
        hold_prob: float = 0.35,
        dominance_margin: float = 0.14,
        confirm_frames: int = 3,
        hangover_frames: int = 4,
        switch_hold_s: float = 2.0,
        target_face_h_px: float = 48.0,
        max_zoom_in_s: float = 2.2,
        landmark_model_path: str | Path | None = None,
    ) -> None:
        self.scorer = SpatiotemporalLipScorer(landmark_model_path)
        self.entry_prob = float(entry_prob)
        self.hold_prob = float(hold_prob)
        self.dominance_margin = float(dominance_margin)
        self.confirm_frames = max(1, int(confirm_frames))
        self.hangover_frames = max(1, int(hangover_frames))
        self.switch_hold_s = float(switch_hold_s)
        self.target_face_h_px = float(target_face_h_px)
        self.max_zoom_in_s = float(max_zoom_in_s)

        self.tracks: dict[int, TrackedFaceState] = {}
        self._next_track_id: int = 1
        self.locked_track_id: int | None = None
        self.locked_since: float = 0.0
        self.candidate_track_id: int | None = None
        self.candidate_frames: int = 0
        self.zoom_used_s: float = 0.0
        self.last_zoom_at: float = 0.0

    def reset(self) -> None:
        """Clear speaker lock & zoom state (call whenever a new seat/preset is locked)."""
        self.tracks.clear()
        self.locked_track_id = None
        self.locked_since = 0.0
        self.candidate_track_id = None
        self.candidate_frames = 0
        self.zoom_used_s = 0.0
        self.last_zoom_at = 0.0

    def record_zoom_pulse(self, duration_s: float, now: float | None = None) -> None:
        self.zoom_used_s += max(0.0, float(duration_s))
        self.last_zoom_at = time.monotonic() if now is None else float(now)

    @staticmethod
    def _center(box: list[float] | np.ndarray) -> tuple[float, float]:
        return float(box[0]) + float(box[2]) * 0.5, float(box[1]) + float(box[3]) * 0.5

    def _match_tracks(self, faces: list[np.ndarray], now: float) -> dict[int, np.ndarray]:
        for tid in list(self.tracks.keys()):
            if (now - self.tracks[tid].last_seen) > 1.8:
                if tid == self.locked_track_id:
                    self.locked_track_id = None
                self.tracks.pop(tid, None)

        matched: dict[int, np.ndarray] = {}
        unmatched_faces = list(range(len(faces)))
        used_tracks: set[int] = set()

        pairs: list[tuple[float, int, int]] = []
        for fi in unmatched_faces:
            fc = self._center(faces[fi])
            fw = float(faces[fi][2])
            for tid, tr in self.tracks.items():
                tc = self._center(tr.box)
                dist = float(np.hypot(fc[0] - tc[0], fc[1] - tc[1]))
                max_d = max(36.0, max(fw, float(tr.box[2])) * 2.2)
                if dist <= max_d:
                    pairs.append((dist, tid, fi))

        pairs.sort(key=lambda item: item[0])
        assigned_faces: set[int] = set()
        for _, tid, fi in pairs:
            if tid in used_tracks or fi in assigned_faces:
                continue
            used_tracks.add(tid)
            assigned_faces.add(fi)
            matched[tid] = faces[fi]

        for fi in unmatched_faces:
            if fi in assigned_faces:
                continue
            tid = self._next_track_id
            self._next_track_id += 1
            self.tracks[tid] = TrackedFaceState(
                track_id=tid,
                box=[float(v) for v in faces[fi][:4]],
                last_seen=now,
            )
            matched[tid] = faces[fi]

        return matched

    def update(
        self,
        faces: list[np.ndarray],
        ref_gray: np.ndarray,
        now: float,
        *,
        hi_res_gray: np.ndarray | None = None,
        quiet: bool = False,
        mic_active: bool | None = None,
    ) -> dict[str, Any]:
        """Score every face in the frame, identify speaker, and return target & zoom guidance."""
        matched = self._match_tracks(faces or [], now)
        per_track: dict[int, dict[str, Any]] = {}

        for tid, face in matched.items():
            tr = self.tracks[tid]
            tr.box = [float(v) for v in face[:4]]
            tr.last_seen = now

            if not quiet:
                energy, prob = self.scorer.update_and_score(tr, face, ref_gray, now, hi_res_frame=hi_res_gray)
            else:
                energy, prob = tr.ema_score, tr.speech_prob

            eff_prob = prob if mic_active is not False else min(prob, 0.25)
            if not tr.speaking:
                if eff_prob >= self.entry_prob:
                    tr.speak_streak += 1
                    if tr.speak_streak >= 2:
                        tr.speaking = True
                        tr.silence_streak = 0
                else:
                    tr.speak_streak = 0
            else:
                if eff_prob < self.hold_prob:
                    tr.silence_streak += 1
                    if tr.silence_streak >= self.hangover_frames:
                        tr.speaking = False
                        tr.speak_streak = 0
                else:
                    tr.silence_streak = 0

            per_track[tid] = {
                "track_id": tid,
                "box": [int(round(v)) for v in tr.box],
                "mouth_box": tr.mouth_rect_ref,
                "mouth_points": tr.mouth_points_ref,
                "inner_mar": round(tr.inner_mar, 3),
                "energy": round(energy, 3),
                "speech_prob": round(prob, 3),
                "speaking": tr.speaking,
            }

        ranked = sorted(
            per_track.values(),
            key=lambda item: (item["speech_prob"], item["energy"]),
            reverse=True,
        )

        locked_present = self.locked_track_id in per_track
        if not quiet and mic_active is not False and ranked:
            top = ranked[0]
            runner_prob = ranked[1]["speech_prob"] if len(ranked) > 1 else 0.0
            top_leads = top["speech_prob"] >= self.entry_prob and (top["speech_prob"] - runner_prob) >= self.dominance_margin

            if not locked_present:
                if top_leads:
                    if self.candidate_track_id == top["track_id"]:
                        self.candidate_frames += 1
                    else:
                        self.candidate_track_id = top["track_id"]
                        self.candidate_frames = 1
                    if self.candidate_frames >= self.confirm_frames:
                        self.locked_track_id = top["track_id"]
                        self.locked_since = now
                        self.candidate_track_id = None
                        self.candidate_frames = 0
                else:
                    self.candidate_track_id = None
                    self.candidate_frames = 0
            elif top["track_id"] != self.locked_track_id and (now - self.locked_since) >= self.switch_hold_s:
                locked_prob = per_track[self.locked_track_id]["speech_prob"]
                if top["speech_prob"] >= max(self.entry_prob, locked_prob + self.dominance_margin):
                    if self.candidate_track_id == top["track_id"]:
                        self.candidate_frames += 1
                    else:
                        self.candidate_track_id = top["track_id"]
                        self.candidate_frames = 1
                    if self.candidate_frames >= self.confirm_frames:
                        self.locked_track_id = top["track_id"]
                        self.locked_since = now
                        self.candidate_track_id = None
                        self.candidate_frames = 0
                        self.zoom_used_s = min(self.zoom_used_s, self.max_zoom_in_s * 0.5)
                else:
                    self.candidate_track_id = None
                    self.candidate_frames = 0

        target_box: list[int] | None = None
        reason = "waiting_for_speaker"
        if self.locked_track_id is not None and self.locked_track_id in per_track:
            target_box = per_track[self.locked_track_id]["box"]
            reason = "lip_sync_locked"
        elif len(per_track) == 1:
            only = next(iter(per_track.values()))
            target_box = only["box"]
            reason = "lip_sync_locked" if only["speaking"] else "single_face_follow"

        zoom_action = "none"
        if target_box is not None and reason == "lip_sync_locked" and not quiet:
            cx = target_box[0] + target_box[2] * 0.5
            fh = float(target_box[3])
            centered = abs(cx - 160.0) <= 55.0
            if centered and fh < (self.target_face_h_px - 8.0) and self.zoom_used_s < self.max_zoom_in_s:
                zoom_action = "in"
            elif fh >= self.target_face_h_px:
                zoom_action = "stop"

        return {
            "locked_track_id": self.locked_track_id,
            "target_box": target_box,
            "reason": reason,
            "zoom_action": zoom_action,
            "landmarks_model": "insightface_2d106det" if self.scorer.landmarks.available else "flux_fallback",
            "tracks": list(per_track.values()),
        }
