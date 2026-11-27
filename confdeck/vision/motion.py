import time
from collections import deque

import numpy as np


def phase_correlation(before, after):
    a = np.asarray(before, dtype=float)
    b = np.asarray(after, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or max(a.shape) > 320:
        raise ValueError("Matching grayscale frames up to 320 pixels are required")
    a, b = a-a.mean(), b-b.mean()
    if a.std() < 1 or b.std() < 1:
        return {"reliable": False, "moved": None, "shift": [0, 0]}
    cross = np.fft.fft2(a)*np.conj(np.fft.fft2(b))
    cross /= np.maximum(abs(cross), 1e-12)
    correlation = abs(np.fft.ifft2(cross))
    index = np.unravel_index(np.argmax(correlation), a.shape)
    shift = [int(i if i <= n//2 else i-n) for i, n in zip(index, a.shape)]
    strength = float(correlation[index]/max(correlation.mean(), 1e-9))
    return {"reliable": strength > 10, "moved": bool(max(map(abs, shift)) > 2) if strength > 10 else None,
            "shift": shift, "peak_ratio": strength}


def learn_llr(speaking, silent):
    if min(len(speaking), len(silent)) < 10:
        raise ValueError("Collect at least ten speaking and silent calibration scores")
    return {"speaking_mean": float(np.mean(speaking)), "silent_mean": float(np.mean(silent)),
            "variance": float(max(.01, (np.var(speaking)+np.var(silent))/2))}


def llr(score, model):
    a, b, variance = model["speaking_mean"], model["silent_mean"], model["variance"]
    return float(np.clip(((score-b)**2-(score-a)**2)/(2*max(.01, variance)), -4, 4))


class MotionEnergy:
    def __init__(self):
        self.previous = None
        self.baselines = {}
        self.last = -1
        self.disabled = False
        self.warning = ""
        self.costs = deque(maxlen=10)
        self.reference = None

    def process(self, frame, rois, now, blocked=False, quiet=False):
        start = time.perf_counter()
        if self.disabled or now-self.last < .19:
            return {}
        self.last = now
        frame = np.asarray(frame, dtype=np.float32)
        if frame.shape != (180, 320) or not np.isfinite(frame).all():
            raise ValueError("Expected 320x180 grayscale frame")
        previous, self.previous = self.previous, frame
        if blocked or previous is None:
            self.previous = None if blocked else frame
            return {}
        diff = frame-previous
        diff -= np.median(diff)  # Reject a global brightness change.
        scores = {}
        for seat, roi in rois.items():
            x, y, w, h = roi["rect"]
            crop = np.abs(diff[y:y+h, x:x+w]).copy()
            for mx, my, mw, mh in roi.get("masks", []):
                crop[max(0, my-y):max(0, min(h, my+mh-y)), max(0, mx-x):max(0, min(w, mx+mw-x))] = 0
            energy = float(np.mean(crop))
            base = self.baselines.setdefault(seat, deque([1.], maxlen=100))
            if quiet:
                base.append(energy)
            scores[seat] = energy/max(1., float(np.median(base)))
        self.costs.append(time.perf_counter()-start)
        if len(self.costs) == 10 and sum(self.costs)/10 > .025:
            self.disabled = True
            self.warning = "Vision disabled: average frame processing exceeded 25 ms"
            return {}
        return scores
