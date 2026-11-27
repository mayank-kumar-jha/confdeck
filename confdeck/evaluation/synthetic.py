"""Seeded sphere clusters, reflections, parked beams and silence level traces."""
import numpy as np

from confdeck.tracking import TrackingSettings, Zone


def generate(seed=7, tag="test"):
    rng = np.random.default_rng(seed)
    settings = TrackingSettings(zones=[Zone(id="a", name="A", azimuth=20, elevation=20, presets={"mock": "1"}),
                                      Zone(id="b", name="B", azimuth=200, elevation=20, presets={"mock": "2"})],
                                wide_presets={"mock": "0"}, level_gate=True)
    rows, labels, t = [], [], 0.0
    for cycle in range(3):
        for seat, condition in [(None, "silence"), ("a", "normal"), ("b", "soft"), ("a", "loud"),
                                (None, "echo"), (None, "transient"), ("b", "moving")]:
            duration = 8
            labels.append({"start": t, "end": t+duration, "seat": seat, "condition": condition})
            for n in range(duration*10):
                center = 20 if seat == "a" else 200
                az, el = np.radians([center, 20])
                v = np.array([np.cos(el)*np.cos(az), np.cos(el)*np.sin(az), np.sin(el)]) + rng.normal(0, .025, 3)
                v /= np.linalg.norm(v)
                a = float(np.degrees(np.arctan2(v[1], v[0])) % 360)
                e = float(np.degrees(np.arcsin(v[2])))
                level = float(rng.normal(-35 if seat else -75, 2))
                if seat and n % 19 == 0:
                    a = (a+180) % 360  # Repeatable ghost direction.
                if condition == "echo":
                    a, e, level = 315., 90., -30.
                if condition == "transient" and n % 15 == 0:
                    level = -20.
                rows.append({"kind": "mic", "t": round(t+n/10, 5),
                             "event": {"azimuth": a, "elevation": e, "level": level}})
            t += duration
    return {"id": f"synthetic-{seed}-{tag}", "tag": tag, "tags": ["synthetic"], "rows": rows, "labels": labels}, settings
