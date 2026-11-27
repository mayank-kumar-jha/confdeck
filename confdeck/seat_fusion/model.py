"""Trainable, scalar-only seat classifier for shadow experiments.

This model is not production calibrated. Its softmax scores are relative model
outputs, not certified probabilities. Train on calibration sessions only and
evaluate on separate labeled room sessions.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from confdeck.evaluation.session import validate
from confdeck.tracking_math.geometry import vector

NO_SPEAKER = "__none__"


def room_signature(settings, links=None):
    data = {"convention": settings.inference.convention.model_dump(), "links": links or {},
            "zones": [{"id": z.id, "mic_id": z.mic_id, "azimuth": z.azimuth,
                       "elevation": z.elevation, "radius": z.radius,
                       "components": [c.model_dump() for c in z.components]}
                      for z in sorted(settings.zones, key=lambda item: item.id)]}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def features(event, speech, convention):
    a, e, level = (event.get(key) for key in ("azimuth", "elevation", "level"))
    reliability = speech.get("reliability") if isinstance(speech, dict) else None
    if (not all(type(x) in (int, float) and math.isfinite(x) for x in (a, e, level, reliability))
            or not 0 <= a <= 360 or not 0 <= e <= 90 or not -90 <= level <= 0
            or not 0 <= reliability <= 1 or event.get("beam_freeze") or event.get("overlap")
            or speech.get("no_signal") or speech.get("clipping")):
        return None
    return [*vector(a, e, convention).tolist(), (level+90)/90, reliability]


class LearnedSeatModel:
    def __init__(self, data, expected_sources=None, expected_seats=None, expected_signature=None):
        if data.get("version") != 1:
            raise ValueError("Unsupported fusion model")
        self.sources = data.get("sources")
        self.classes = data.get("classes")
        if (not isinstance(self.sources, list) or len(self.sources) < 2 or len(set(self.sources)) != len(self.sources)
                or not isinstance(self.classes, list) or len(self.classes) < 2
                or len(set(self.classes)) != len(self.classes) or NO_SPEAKER not in self.classes):
            raise ValueError("Invalid fusion model labels")
        if expected_sources is not None and self.sources != sorted(expected_sources):
            raise ValueError("Fusion model microphone map does not match the room")
        if expected_seats is not None and set(self.classes) != set(expected_seats) | {NO_SPEAKER}:
            raise ValueError("Fusion model seat map does not match the room")
        if expected_signature is not None and data.get("room_signature") != expected_signature:
            raise ValueError("Fusion model calibration does not match the current room map")
        self.mean = np.asarray(data.get("mean"), dtype=float)
        self.scale = np.asarray(data.get("scale"), dtype=float)
        self.weights = np.asarray(data.get("weights"), dtype=float)
        self.bias = np.asarray(data.get("bias"), dtype=float)
        dim = 5*len(self.sources)
        if (self.mean.shape != (dim,) or self.scale.shape != (dim,)
                or self.weights.shape != (dim, len(self.classes)) or self.bias.shape != (len(self.classes),)
                or not all(np.isfinite(x).all() for x in (self.mean, self.scale, self.weights, self.bias))
                or (self.scale <= 0).any()):
            raise ValueError("Invalid fusion model parameters")

    @classmethod
    def load(cls, path, **kwargs):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), **kwargs)

    def predict(self, samples):
        if set(samples) != set(self.sources):
            raise ValueError("Missing independent microphone features")
        x = np.asarray([v for source in self.sources for v in samples[source]], dtype=float)
        if x.shape != self.mean.shape or not np.isfinite(x).all():
            raise ValueError("Invalid fusion features")
        logits = ((x-self.mean)/self.scale) @ self.weights + self.bias
        if not np.isfinite(logits).all():
            raise ValueError("Fusion model produced invalid scores")
        probabilities = np.exp(logits-np.max(logits))
        probabilities /= probabilities.sum()
        return dict(zip(self.classes, probabilities.tolist()))


def calibration_examples(sessions, settings, interval=.2, max_age=.45, links=None):
    if any(z.microphone_profiles for z in settings.zones):
        raise ValueError("Experimental fusion training does not support shared microphone profiles")
    sources = sorted({z.mic_id for z in settings.zones})
    if len(sources) < 2 or len(sessions) < 2:
        raise ValueError("Use calibration recordings from at least two separate sessions")
    ids, samples, targets, groups = set(), [], [], []
    links = links or {}
    seats = {z.id for z in settings.zones}
    if any(source not in seats or target not in seats for source, target in links.items()):
        raise ValueError("Seat links must reference existing zones")
    for session in sessions:
        validate(session, scoring=False)
        identity = session.get("id")
        if session.get("tag") != "calibration" or not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("Training requires distinct, explicitly tagged calibration sessions")
        if any(row.get("kind") == "health" and row.get("dropped", 0) for row in session.get("rows", [])):
            raise ValueError("Calibration recording dropped events")
        ids.add(identity)
        labels = session.get("labels") or [r for r in session.get("rows", []) if r.get("kind") == "label"]
        for label in labels:
            if (label.get("seat") not in seats | {None} or type(label.get("start")) not in (int, float)
                    or type(label.get("end")) not in (int, float) or not label["start"] < label["end"]):
                raise ValueError("Invalid calibration label")
        mic, audio = {}, {}
        last_sample = -math.inf
        for row in session.get("rows", []):
            if "t" not in row:
                continue
            t = row["t"]
            if row.get("kind") == "mic":
                ev = row.get("event", {})
                source = ev.get("mic_id", "primary")
                if source in sources:
                    mic[source] = (t, ev)
            elif row.get("kind") == "speech_frame" and row.get("mic_id") in sources:
                audio[row["mic_id"]] = (t, row)
            if t-last_sample < interval or set(mic) != set(sources) or set(audio) != set(sources):
                continue
            active = [label for label in labels if label["start"] <= t < label["end"]]
            if len(active) != 1 or active[0].get("condition") == "overlap":
                continue
            if any(not 0 <= t-mic[s][0] <= max_age or not 0 <= t-audio[s][0] <= max_age for s in sources):
                continue
            values = [features(mic[s][1], audio[s][1], settings.inference.convention) for s in sources]
            if any(x is None for x in values):
                continue
            samples.append([v for entry in values for v in entry])
            seat = active[0].get("seat")
            targets.append(links.get(seat, seat) if seat else NO_SPEAKER)
            groups.append(identity)
            last_sample = t
    return sources, samples, targets, groups


def train(sessions, settings, min_examples=20, steps=500, rate=.15, regularization=.02, links=None):
    if type(min_examples) is not int or min_examples < 1 or type(steps) is not int or not 1 <= steps <= 10000:
        raise ValueError("Invalid model training settings")
    if not all(type(x) in (float, int) and math.isfinite(x) and x > 0 for x in (rate, regularization)):
        raise ValueError("Invalid model training settings")
    links = links or {}
    sources, rows, targets, groups = calibration_examples(sessions, settings, links=links)
    classes = sorted({links.get(z.id, z.id) for z in settings.zones}) + [NO_SPEAKER]
    if any(target not in classes for target in targets):
        raise ValueError("Calibration contains an unknown seat")
    for label in classes:
        chosen = [i for i, target in enumerate(targets) if target == label]
        if len(chosen) < min_examples or len({groups[i] for i in chosen}) < 2:
            raise ValueError(f"Need at least {min_examples} samples of {label} across two sessions")
    x = np.asarray(rows, dtype=float)
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), .05)
    x = (x-mean)/scale
    y = np.asarray([classes.index(target) for target in targets])
    counts = np.bincount(y, minlength=len(classes))
    sample_weights = 1/counts[y]
    sample_weights /= sample_weights.sum()
    weights = np.zeros((x.shape[1], len(classes)))
    bias = np.zeros(len(classes))
    onehot = np.eye(len(classes))[y]
    for _ in range(steps):
        logits = x @ weights + bias
        prob = np.exp(logits-logits.max(axis=1, keepdims=True))
        prob /= prob.sum(axis=1, keepdims=True)
        error = (prob-onehot)*sample_weights[:, None]
        weights -= rate * (x.T @ error + regularization*weights)
        bias -= rate * error.sum(axis=0)
    return {"version": 1, "sources": sources, "classes": classes,
            "room_signature": room_signature(settings, links),
            "mean": mean.tolist(), "scale": scale.tolist(), "weights": weights.tolist(), "bias": bias.tolist(),
            "training_sessions": len(set(groups)), "examples_per_class": {label: int(counts[i]) for i, label in enumerate(classes)},
            "warning": "Experimental model trained on calibration data; validate on separate rooms and sessions."}
