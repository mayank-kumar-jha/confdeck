import math

import numpy as np

from .geometry import vector


def tolerance_kappa(degrees):
    return min(10000., math.log(2) / max(1e-6, 1-math.cos(math.radians(degrees))))


def fit(points, tolerance=25):
    """Robust spherical clusters retain supported reflections; isolated points are clutter."""
    x = np.asarray(points, dtype=float)
    if x.ndim != 2 or x.shape[1] != 3 or len(x) < 10 or not np.isfinite(x).all():
        raise ValueError("At least ten finite unit vectors are required")
    x = x / np.linalg.norm(x, axis=1, keepdims=True)
    remaining = np.ones(len(x), dtype=bool)
    components = []
    threshold = math.cos(math.radians(tolerance))
    for _ in range(3):
        pool = x[remaining]
        if len(pool) < max(5, len(x)*.05):
            break
        # Bounded candidate search, avoiding a quadratic full-session allocation.
        candidates = pool[::max(1, len(pool)//100)]
        support = pool @ candidates.T
        center = candidates[np.argmax((support >= threshold).sum(axis=0))]
        members = remaining & (x @ center >= threshold)
        cluster = x[members]
        if len(cluster) < max(5, len(x)*.05):
            break
        mean = cluster.mean(axis=0)
        mean /= np.linalg.norm(mean)
        errors = np.arccos(np.clip(cluster @ mean, -1, 1))
        cutoff = np.median(errors) + 3*max(.01, np.median(np.abs(errors-np.median(errors))))
        cluster = cluster[errors <= cutoff]
        resultant = cluster.mean(axis=0)
        r = float(np.linalg.norm(resultant))
        kappa = float(np.clip(r*(3-r*r)/max(1e-6, 1-r*r), .01, 10000))
        components.append({"mean": (resultant/r).tolist(), "kappa": kappa, "weight": len(cluster)})
        remaining[members] = False
    if not components:
        raise ValueError("No repeatable direction; collect more speech")
    total = sum(c["weight"] for c in components)
    for component in components:
        component["weight"] /= total
    return components


class SeatModel:
    def __init__(self, zones, convention):
        self.ids = [z.id for z in zones]
        self.components = []
        for z in zones:
            cs = [c.model_dump() for c in z.components] if z.components else [
                {"mean": vector(z.azimuth, z.elevation, convention), "kappa": tolerance_kappa(z.radius), "weight": 1}]
            self.components.append(cs)
        self.means = np.asarray([c["mean"] for cs in self.components for c in cs]).reshape(-1, 3)
        self.kappa = np.asarray([c["kappa"] for cs in self.components for c in cs])
        weights = np.asarray([c["weight"] for cs in self.components for c in cs])
        self.owner = np.asarray([i for i, cs in enumerate(self.components) for _ in cs])
        # Density divided by uniform sphere density, evaluated without exp(kappa).
        self.norm = 2*self.kappa/(-np.expm1(-2*self.kappa))*weights

    def likelihood(self, v):
        densities = self.norm * np.exp(self.kappa * np.minimum(0, self.means @ v - 1))
        return .03 + .97*np.bincount(self.owner, weights=densities, minlength=len(self.ids))


def calibrate(session, zones, convention):
    from confdeck.evaluation.session import validate

    from .config import Component
    from .geometry import angles
    validate(session, scoring=False)
    if session["tag"] != "calibration":
        raise ValueError("Only calibration sessions may fit seat models")
    labels = session.get("labels") or [r for r in session["rows"] if r.get("kind") == "label"]
    fitted, held_out = [], {}
    for zone in zones:
        intervals = [r for r in labels if r["seat"] == zone.id]
        if sum(r["end"]-r["start"] for r in intervals) < 20:
            raise ValueError(f"{zone.name}: collect at least 20 seconds across soft, normal and loud speech")
        samples = [r for r in session["rows"] if r.get("kind") == "mic" and any(label["start"] <= r["t"] < label["end"] for label in intervals)
                   and r["event"].get("mic_id", "primary") == zone.mic_id
                   and not r["event"].get("beam_freeze") and r["event"].get("level", -90) is not None
                   and r["event"].get("level", -90) > -65]
        if len(samples) < 40 or samples[-1]["t"]-samples[0]["t"] < 19:
            raise ValueError(f"{zone.name}: insufficient fresh speech data")
        vectors = [vector(r["event"]["azimuth"], r["event"]["elevation"], convention) for r in samples]
        # Hold out contiguous one-second blocks to avoid frame-level train/test leakage.
        train = [v for r, v in zip(samples, vectors) if int(r['t']) % 4 != 3]
        held_out[zone.id] = [v for r, v in zip(samples, vectors) if int(r['t']) % 4 == 3]
        cs = fit(train, zone.radius)
        center = angles(cs[0]["mean"], convention)
        fitted.append(zone.model_copy(update={"components": [Component(**c) for c in cs], "azimuth": center[0], "elevation": center[1]}))
    models = {source: SeatModel([z for z in fitted if z.mic_id == source], convention)
              for source in {z.mic_id for z in fitted}}
    separability = {}
    for seat, vectors in held_out.items():
        model = models[next(z.mic_id for z in fitted if z.id == seat)]
        scores = [model.ids[int(np.argmax(model.likelihood(v)))] == seat for v in vectors]
        separability[seat] = float(np.mean(scores)) if scores else None
    # Return training-block fits; the held-out blocks remain independent diagnostics.
    return {"zones": [z.model_dump() for z in fitted], "separability": separability,
            "warnings": [f"{s}: seats may not be separable" for s, score in separability.items() if score is None or score < .85],
            "calibration_id": session.get("id")}
