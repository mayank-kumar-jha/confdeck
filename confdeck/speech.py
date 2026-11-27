"""Offline speech evidence. No device access, downloads, or audio persistence.

Import only when requested. Audio and position levels are separate domains;
the adaptive threshold below is exclusively in PCM dBFS.
"""
import math
from collections import deque
from dataclasses import dataclass
from statistics import median


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class GateConfig:
    margin: float = 3.
    p_on: float = .35
    p_off: float = .20
    low_voice: bool = True
    offset: float = 0.  # position timestamp = audio timestamp + offset

    def __post_init__(self):
        if not all(finite(v) for v in (self.margin, self.p_on, self.p_off, self.offset)):
            raise ValueError("Gate settings must be finite")
        if not (0 <= self.margin <= 20 and 0 < self.p_off < self.p_on <= 1 and abs(self.offset) <= 2):
            raise ValueError("Invalid speech thresholds or clock offset")


class SpeechGate:
    """Consumes one scalar record per 512-sample / 16 kHz frame.

    Two consecutive frames open the gate. Hangover retains the speech state,
    but low-probability frames never add seat evidence. Missing/stale frames
    explicitly request the existing level-only fallback.
    """
    def __init__(self, config=None):
        self.config = config or GateConfig()
        self.floor = -75.
        self.floor_samples = deque(maxlen=938)  # ~30 s, scalar metadata only
        self.frames = deque(maxlen=32)
        self.last = self.quiet_since = self.below_since = None
        self.active = False
        self.onset_count = 0
        self.status = "Audio unavailable; using existing level-only gate"

    def push(self, timestamp, level, probability):
        if not all(finite(v) for v in (timestamp, level, probability)) or not -120 <= level <= 0 or not 0 <= probability <= 1:
            raise ValueError("Invalid speech frame")
        t = timestamp + self.config.offset
        if self.last is not None and t <= self.last:
            raise ValueError("Audio frames must be strictly ordered")
        if self.last is not None and t-self.last > .500:
            self.active = False
            self.onset_count = 0
            self.quiet_since = self.below_since = None
            self.frames.clear()
        self.last = t
        c = self.config
        # Only confidently non-speech frames can train the noise floor.
        if probability < c.p_off:
            self.floor_samples.append((t, level))
            while self.floor_samples and t-self.floor_samples[0][0] > 30:
                self.floor_samples.popleft()
            values = sorted(v for _, v in self.floor_samples)
            self.floor = values[int((len(values)-1)*.1)]
        threshold = self.floor+c.margin
        margin = level-threshold
        near = 0 < margin <= 6 and probability > max(c.p_off, .4)
        if near:
            if self.quiet_since is None:
                self.quiet_since = t
        else:
            self.quiet_since = None
        low = c.low_voice and self.quiet_since is not None and t-self.quiet_since >= .15
        onset = min(c.p_on, .4) if low else c.p_on
        transition = None
        # Onset: trigger if VAD probability exceeds onset threshold OR margin > 0 with reasonable probability
        speech_candidate = (probability >= onset) or (margin > 0 and probability >= c.p_off)
        if not self.active:
            self.onset_count = self.onset_count + 1 if speech_candidate else 0
            if self.onset_count >= 2:
                self.active = True
                transition = "speech_start"
        elif probability < c.p_off and margin <= 0:
            if self.below_since is None:
                self.below_since = t
            if t - self.below_since >= .35:
                self.active = False
                self.onset_count = 0
                transition = "speech_end"
        else:
            self.below_since = None
            
        is_speech = (self.active and probability >= c.p_off) or (probability >= onset and level >= -72.0)
        weight = float(probability) if is_speech else 0.
        self.status = "speech" if is_speech else ("noise" if (margin > 0 or probability > 0.15) else "silence")
        frame = dict(time=t, level=level, probability=probability, threshold=threshold,
                     reliability=weight, active=self.active, transition=transition)
        self.frames.append(frame)
        return frame

    def evidence(self, now):
        # Never attach a future frame or replay one across a device outage.
        frame = next((f for f in reversed(self.frames) if f["time"] <= now), None)
        if frame is None or now-frame["time"] > .350:
            self.status = "Audio unavailable; using existing level-only gate"
            return None
        return dict(frame)


class SileroFrames:
    """Local Silero ONNX recurrent interface: 512 samples + 64 context.

    Model must be supplied by the operator. Only CPU, one inference thread.
    Reference: github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py
    """
    def __init__(self, model_path):
        from pathlib import Path
        if not Path(model_path).is_file():
            raise ValueError("Missing local Silero ONNX model")
        import numpy as np
        import onnxruntime as ort
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        self.state = self.np.zeros((2, 1, 128), dtype=self.np.float32)
        self.context = self.np.zeros((1, 64), dtype=self.np.float32)

    def process(self, samples):
        np = self.np
        pcm = np.asarray(samples, dtype=np.float32)
        if pcm.shape != (512,) or not np.isfinite(pcm).all() or np.max(np.abs(pcm)) > 1:
            raise ValueError("Expected 512 finite mono PCM samples in [-1, 1]")
        inputs = np.concatenate((self.context, pcm.reshape(1, 512)), axis=1)
        output, state = self.session.run(None, {"input": inputs, "state": self.state,
                                               "sr": np.array(16000, dtype=np.int64)})
        probability = float(np.asarray(output).reshape(-1)[0])
        if not finite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid VAD probability")
        self.state, self.context = state, pcm[-64:].reshape(1, 64).copy()
        level = max(-120., 20*math.log10(max(1e-6, float(np.sqrt(np.mean(pcm**2))))))
        return dict(level=level, probability=probability,
                    clipping=bool(np.max(np.abs(pcm)) >= .999), no_signal=level <= -100)


def estimate_sync(audio_peaks, position_peaks):
    """Operator-paired clap timestamps; reject ambiguous/inconsistent pairs."""
    if len(audio_peaks) != len(position_peaks) or len(audio_peaks) < 3:
        raise ValueError("Capture at least three paired claps")
    if not all(finite(t) for t in [*audio_peaks, *position_peaks]):
        raise ValueError("Invalid clap timestamps")
    if any(b <= a for values in (audio_peaks, position_peaks) for a, b in zip(values, values[1:])):
        raise ValueError("Claps must be ordered")
    offsets = [p-a for a, p in zip(audio_peaks, position_peaks)]
    offset = median(offsets)
    if abs(offset) > 2 or max(abs(v-offset) for v in offsets) > .05:
        raise ValueError("Clap timing is inconsistent; repeat sync check")
    return offset


def passes_guard(before, after):
    keys = ("wrong_rate", "false_triggers_per_10min", "quiet_lock_rate", "median_latency")
    if any(not finite(report.get(k)) for report in (before, after) for k in keys):
        return False
    return (after["wrong_rate"] <= before["wrong_rate"]
            and after["false_triggers_per_10min"] < before["false_triggers_per_10min"]
            and after["quiet_lock_rate"] > before["quiet_lock_rate"]
            and after["median_latency"] <= before["median_latency"]+.1+1e-9)


def suggest_calibration(phases, max_noise_per_10min=0.):
    """Scalar-only suggestion; caller must review/apply explicitly.

    Each phase contains consecutive 32 ms (level, probability) pairs. This is
    a calibration result, never a held-out accuracy claim.
    """
    if not finite(max_noise_per_10min) or max_noise_per_10min < 0:
        raise ValueError("Invalid target noise rate")
    for name in ("silence", "normal", "quiet", "noise"):
        values = phases.get(name, [])
        minimum = 313 if name == "silence" else 32
        if not minimum <= len(values) <= 1875:
            raise ValueError(f"Insufficient or excessive {name} frames")
        for level, probability in values:
            if not finite(level) or not finite(probability) or not -120 <= level <= 0 or not 0 <= probability <= 1:
                raise ValueError("Invalid calibration frame")
    candidates = []
    for margin in (4., 5., 6.):
        for p_on in (.4, .5, .6, .7):
            for p_off in (.25, .35):
                counts = {}
                for name in ("normal", "quiet", "noise"):
                    gate = SpeechGate(GateConfig(margin, p_on, p_off))
                    t = 0.
                    for level, probability in phases["silence"]:
                        gate.push(t, level, probability)
                        t += .032
                    counts[name] = 0
                    for level, probability in phases[name]:
                        frame = gate.push(t, level, probability)
                        counts[name] += frame["transition"] == "speech_start"
                        t += .032
                rate = counts["noise"]*600/(len(phases["noise"])*.032)
                if counts["normal"] and counts["quiet"] and rate <= max_noise_per_10min:
                    candidates.append(dict(margin=margin, p_on=p_on, p_off=p_off,
                                           quiet_detected=True, noise_triggers=counts["noise"],
                                           noise_per_10min=rate))
    if not candidates:
        return dict(suggestion=None, quiet_detected=False,
                    reason="No tested thresholds catch normal and quiet voice within the noise limit")
    best = min(candidates, key=lambda x: (x["noise_triggers"], -x["p_on"], -x["margin"], -x["p_off"]))
    return dict(suggestion=best, requires_confirmation=True,
                note="Calibration only; validate on a separate labelled test session")
