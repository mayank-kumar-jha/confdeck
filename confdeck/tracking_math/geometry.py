import math

import numpy as np

from .config import Convention


def vector(azimuth, elevation, convention=None):
    c = convention or Convention()
    a = math.radians(c.azimuth_zero + c.orientation_offset + (-azimuth if c.clockwise else azimuth))
    e = math.radians(90-elevation if c.elevation_reference == "vertical" else elevation)
    return np.array([math.cos(e)*math.cos(a), math.cos(e)*math.sin(a), math.sin(e)])


def distance(a, b):
    return math.degrees(math.acos(float(np.clip(np.dot(a, b), -1, 1))))


def angles(v, convention=None):
    c = convention or Convention()
    a = math.degrees(math.atan2(v[1], v[0])) - c.azimuth_zero - c.orientation_offset
    e = math.degrees(math.asin(float(np.clip(v[2], -1, 1))))
    return ((-a if c.clockwise else a) % 360, 90-e if c.elevation_reference == "vertical" else e)


def nadir_check(samples, convention=None):
    if len(samples) < 10:
        raise ValueError("Stand directly under the mic and collect at least ten samples")
    errors = [distance(vector(a, e, convention), np.array([0., 0., 1.])) for a, e in samples]
    return {"passed": bool(np.median(errors) <= 10), "median_error": float(np.median(errors)),
            "instruction": "Stand directly under the microphone; verify the elevation reference before calibration."}


def estimate_offset(observed, expected):
    differences = np.radians(np.asarray(expected)-np.asarray(observed))
    if len(differences) < 2:
        raise ValueError("Use at least two known seat bearings")
    return math.degrees(math.atan2(float(np.sin(differences).mean()), float(np.cos(differences).mean())))


def local_angles_to_room_point(
    azimuth: float,
    elevation: float,
    *,
    ceiling_height: float = 3.0,
    head_height: float = 1.2,
    room_x: float = 0.0,
    room_y: float = 0.0,
    room_yaw: float = 0.0,
    azimuth_offset: float = 0.0,
    invert: bool = False,
    elevation_reference: str = "vertical",
) -> tuple[float, float] | None:
    """Project local (azimuth, elevation) onto the horizontal head plane in room meters.

    Pass raw device azimuth with `azimuth_offset`/`invert`, or already-adjusted azimuth with
    `azimuth_offset=0.0, invert=False`; `room_yaw` then rotates local axes into room frame.
    """
    if not (math.isfinite(azimuth) and math.isfinite(elevation)):
        return None
    drop = float(ceiling_height) - float(head_height)
    if drop <= 0:
        return None
    nadir_angle = float(elevation) if elevation_reference == "vertical" else 90.0 - float(elevation)
    if nadir_angle >= 85.0 or nadir_angle < 0.0:
        return None
    local_az = ((-1.0 if invert else 1.0) * float(azimuth) + float(azimuth_offset) + float(room_yaw)) % 360.0
    radius = drop * math.tan(math.radians(nadir_angle))
    rad = math.radians(local_az)
    return (round(float(room_x) + radius * math.sin(rad), 2), round(float(room_y) + radius * math.cos(rad), 2))


def fuse_room_coordinates(candidates: list[dict]) -> dict | None:
    """Fuse multi-mic room (x, y) candidates weighted by SNR, confidence, and recency."""
    valid = []
    for item in candidates or []:
        if item.get("excluded"):
            continue
        x, y = item.get("x"), item.get("y")
        if not (isinstance(x, (int, float)) and isinstance(y, (int, float)) and math.isfinite(x) and math.isfinite(y)):
            continue
        snr = max(0.0, float(item.get("snr_db", item.get("level_db", -50.0) - item.get("noise_floor_db", -70.0))))
        conf = max(0.05, min(1.0, float(item.get("confidence", 0.5))))
        age = max(0.0, float(item.get("age_s", 0.0)))
        if age > 1.5:
            continue
        recency = max(0.1, 1.0 - age / 1.5)
        weight = max(1e-3, (1.0 + snr / 6.0) * conf * recency)
        valid.append((str(item.get("mic_id", "")), float(x), float(y), weight))
    if not valid:
        return None
    total_w = sum(v[3] for v in valid)
    fx = sum(v[1] * v[3] for v in valid) / total_w
    fy = sum(v[2] * v[3] for v in valid) / total_w
    spread = max(math.hypot(v[1] - fx, v[2] - fy) for v in valid) if len(valid) > 1 else 0.0
    return {
        "x": round(fx, 2),
        "y": round(fy, 2),
        "sources": [v[0] for v in valid],
        "weights": {v[0]: round(v[3] / total_w, 3) for v in valid if v[0]},
        "spread_m": round(spread, 2),
    }
