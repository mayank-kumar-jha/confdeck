"""YOLOX ONNX person detector for teaching zone body tracking.

Loads YOLOX-Tiny (or YOLOX-Nano fallback) ONNX model via onnxruntime (with
OpenCV DNN fallback when onnxruntime is unavailable) and detects "person"
class (COCO class 0) bounding boxes.

License: Apache-2.0 (YOLOX model from Megvii-BaseDetection)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("confdeck.vision.body")

_MODEL_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "vision-model"
DEFAULT_YOLOX_PATH = _MODEL_DIR / "yolox_tiny.onnx"
FALLBACK_YOLOX_PATH = _MODEL_DIR / "yolox_nano.onnx"

# COCO class 0 = person
_PERSON_CLASS = 0


class YOLOXPersonDetector:
    """Single-class YOLOX ONNX person detector supporting ONNX Runtime and OpenCV DNN."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        input_size: tuple[int, int] | None = None,
        conf_threshold: float = 0.32,
        nms_threshold: float = 0.45,
    ) -> None:
        self._explicit_model = bool(model_path)
        self._explicit_input_size = input_size is not None
        if model_path:
            self.model_path = Path(model_path)
        elif DEFAULT_YOLOX_PATH.is_file():
            self.model_path = DEFAULT_YOLOX_PATH
        else:
            self.model_path = FALLBACK_YOLOX_PATH
        if input_size is not None:
            self.input_w, self.input_h = int(input_size[0]), int(input_size[1])
        else:
            self.input_w, self.input_h = (416, 416)
        self.conf_threshold = float(conf_threshold)
        self.nms_threshold = float(nms_threshold)
        self.session = None
        self.dnn_net = None
        self.backend: str = ""
        self._input_name: str = ""
        self._output_name: str = ""
        self._strides: list[int] = []
        self._grids: np.ndarray | None = None
        self._expanded_strides: np.ndarray | None = None
        self._warmup_done = False
        self._last_cost_ms: float = 0.0

        if self.model_path.is_file():
            self._load()

    def _load(self) -> None:
        # 1. Prefer ONNX Runtime (fast AVX2/AVX-VNNI CPU execution: ~6-20 ms on Mini PC)
        try:
            import onnxruntime as ort

            self._build_grids()
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = 4
            opts.inter_op_num_threads = 1
            self.session = ort.InferenceSession(
                str(self.model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._input_name = self.session.get_inputs()[0].name
            self._output_name = self.session.get_outputs()[0].name
            self.backend = "onnxruntime"
            log.info(
                "YOLOXPersonDetector loaded %s via onnxruntime (%dx%d)",
                self.model_path.name,
                self.input_w,
                self.input_h,
            )
            return
        except Exception as exc:
            log.debug("ONNX Runtime unavailable (%s), trying OpenCV DNN", exc)
            self.session = None

        # 2. Fallback to OpenCV DNN ONNX loader: use yolox_nano @ 320x320 for real-time Mini PC CPU speed
        if not self._explicit_model and FALLBACK_YOLOX_PATH.is_file():
            self.model_path = FALLBACK_YOLOX_PATH
        if not self._explicit_input_size:
            self.input_w, self.input_h = (320, 320)
        self._build_grids()

        try:
            net = cv2.dnn.readNetFromONNX(str(self.model_path))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self.dnn_net = net
            self.backend = "opencv_dnn"
            log.info(
                "YOLOXPersonDetector loaded %s via OpenCV DNN (%dx%d)",
                self.model_path.name,
                self.input_w,
                self.input_h,
            )
        except Exception as exc:
            log.warning("Failed to load YOLOX model %s: %s", self.model_path, exc)
            self.dnn_net = None
            self.backend = ""

    def _build_grids(self) -> None:
        """Pre-compute YOLOX anchor grids for all stride levels."""
        self._strides = [8, 16, 32]
        grids = []
        expanded = []
        for s in self._strides:
            h_s = self.input_h // s
            w_s = self.input_w // s
            yv, xv = np.meshgrid(np.arange(h_s), np.arange(w_s), indexing="ij")
            grid = np.stack([xv, yv], axis=-1).reshape(-1, 2).astype(np.float32)
            grids.append(grid)
            expanded.append(np.full((grid.shape[0], 1), s, dtype=np.float32))
        self._grids = np.concatenate(grids, axis=0)
        self._expanded_strides = np.concatenate(expanded, axis=0)

    @property
    def available(self) -> bool:
        return self.session is not None or self.dnn_net is not None

    def _preprocess(self, bgr: np.ndarray) -> tuple[np.ndarray, float]:
        """Megvii-standard top-left letterbox padding in BGR float32 (0..255)."""
        img_h, img_w = bgr.shape[:2]
        ratio = min(self.input_w / img_w, self.input_h / img_h)
        new_w = max(1, int(img_w * ratio))
        new_h = max(1, int(img_h * ratio))
        resized = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        padded = np.full((self.input_h, self.input_w, 3), 114, dtype=np.uint8)
        padded[:new_h, :new_w] = resized

        blob = padded.transpose(2, 0, 1)[np.newaxis, ...]
        blob = np.ascontiguousarray(blob, dtype=np.float32)
        return blob, ratio

    def _postprocess(
        self,
        output: np.ndarray,
        ratio: float,
        orig_w: int,
        orig_h: int,
    ) -> list[list[int | float]]:
        """Decode YOLOX raw output to person bboxes [x, y, w, h, conf] in original frame coords."""
        preds = output[0].copy()  # (N, 85)

        if self._grids is not None and preds.shape[0] == self._grids.shape[0]:
            preds[:, :2] = (preds[:, :2] + self._grids) * self._expanded_strides
            preds[:, 2:4] = np.exp(preds[:, 2:4]) * self._expanded_strides

        boxes_cxcywh = preds[:, :4]
        x1 = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] * 0.5
        y1 = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] * 0.5
        x2 = boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] * 0.5
        y2 = boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] * 0.5

        scores = preds[:, 4] * preds[:, 5 + _PERSON_CLASS]
        mask = scores >= self.conf_threshold
        if not np.any(mask):
            return []

        fx1 = np.clip(x1[mask] / ratio, 0, orig_w)
        fy1 = np.clip(y1[mask] / ratio, 0, orig_h)
        fx2 = np.clip(x2[mask] / ratio, 0, orig_w)
        fy2 = np.clip(y2[mask] / ratio, 0, orig_h)
        fscores = scores[mask]

        boxes_for_nms = np.stack([fx1, fy1, fx2 - fx1, fy2 - fy1], axis=-1).tolist()
        indices = cv2.dnn.NMSBoxes(
            boxes_for_nms, fscores.tolist(), self.conf_threshold, self.nms_threshold
        )
        if indices is None or len(indices) == 0:
            return []

        results: list[list[int | float]] = []
        for idx in indices:
            i = int(idx) if np.ndim(idx) == 0 else int(idx[0])
            bx = max(0, int(round(fx1[i])))
            by = max(0, int(round(fy1[i])))
            bw = max(1, int(round(fx2[i] - fx1[i])))
            bh = max(1, int(round(fy2[i] - fy1[i])))
            if bw < 8 or bh < 12:
                continue
            results.append([bx, by, bw, bh, round(float(fscores[i]), 3)])
        results.sort(key=lambda d: d[4], reverse=True)
        return results

    def detect_persons(self, bgr: np.ndarray) -> list[list[int | float]]:
        """Detect persons in a BGR frame, returning [[x, y, w, h, conf], ...]."""
        if not self.available or bgr is None or bgr.size == 0:
            return []

        t0 = time.perf_counter()
        orig_h, orig_w = bgr.shape[:2]
        blob, ratio = self._preprocess(bgr)

        try:
            if self.session is not None:
                output = self.session.run(
                    [self._output_name], {self._input_name: blob}
                )[0]
            else:
                self.dnn_net.setInput(blob)
                output = self.dnn_net.forward()
        except Exception as exc:
            log.warning("YOLOX inference failed: %s", exc)
            return []

        results = self._postprocess(output, ratio, orig_w, orig_h)
        self._last_cost_ms = (time.perf_counter() - t0) * 1000.0

        if not self._warmup_done:
            self._warmup_done = True
            log.info(
                "YOLOX warmup (%s): %.1f ms, %d persons",
                self.backend,
                self._last_cost_ms,
                len(results),
            )
        return results

    def detect_persons_in_roi(
        self,
        bgr: np.ndarray,
        roi: tuple[int, int, int, int],
        min_overlap: float = 0.25,
        masks: list | None = None,
        prev_box: list[int] | None = None,
    ) -> list[list[int | float]]:
        """Detect persons overlapping ROI, filtering out masked areas and ranking with continuity."""
        all_persons = self.detect_persons(bgr)
        rx, ry, rw, rh = roi
        rx2, ry2 = rx + rw, ry + rh

        scored: list[tuple[float, list[int | float]]] = []
        for det in all_persons:
            bx, by, bw, bh, conf = det
            pcx, pcy = bx + bw * 0.5, by + bh * 0.5

            # Reject if center lies in an exclusion mask
            masked = False
            for m in masks or []:
                if isinstance(m, (list, tuple)) and len(m) >= 4:
                    mx, my, mw, mh = [float(v) for v in m[:4]]
                    if mx <= pcx <= (mx + mw) and my <= pcy <= (my + mh):
                        masked = True
                        break
            if masked:
                continue

            ox1 = max(bx, rx)
            oy1 = max(by, ry)
            ox2 = min(bx + bw, rx2)
            oy2 = min(by + bh, ry2)
            if ox2 <= ox1 or oy2 <= oy1:
                continue
            overlap_area = (ox2 - ox1) * (oy2 - oy1)
            person_area = max(1, bw * bh)
            if overlap_area / person_area < min_overlap:
                continue

            # Rank by confidence + continuity boost near previously locked teacher box
            rank = float(conf)
            if prev_box is not None and len(prev_box) >= 4:
                px, py, pw, ph = [float(v) for v in prev_box[:4]]
                dist = np.hypot((pcx - (px + pw * 0.5)), (pcy - (py + ph * 0.5)))
                if dist < max(35.0, float(pw) * 1.4):
                    rank += 0.25 * (1.0 - dist / max(35.0, float(pw) * 1.4))
            scored.append((rank, det))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored]

    @property
    def last_cost_ms(self) -> float:
        return self._last_cost_ms

    def close(self) -> None:
        self.session = None
        self.dnn_net = None
        self.backend = ""
