"""Bounded, camera-free replay of recent microphone observations."""
import math

from confdeck.evaluation.replay import make_engine


def preview(rows, settings):
    shared = any(zone.microphone_profiles for zone in settings.zones)
    engine = make_engine(settings.model_copy(deep=True), settings.engine,
                         speech=shared and settings.engine == 'new')
    points = []
    previous = rows[0]["t"] if rows else 0
    for row in rows:
        now = row["t"]
        while previous + .1 < now:
            previous += .1
            engine.tick(previous)
        event = dict(row['event'])
        if shared and settings.engine == 'new' and not isinstance(event.get('speech'), dict):
            event['speech'] = {'reliability': 0}
        engine.feed(event, now)
        previous = now
        event = row["event"]
        if all(type(event.get(k)) in (int, float) and math.isfinite(event[k]) for k in ("azimuth", "elevation")):
            points.append({"time": now, "azimuth": event["azimuth"], "elevation": event["elevation"],
                           "mic_id": event.get("mic_id", "primary"),
                           "accepted": engine.last_sample == now, "reason": engine.reason,
                           "seat": engine.current, "candidate": engine.candidate})
    return {"events": points, "accepted": sum(p["accepted"] for p in points),
            "rejected": sum(not p["accepted"] for p in points),
            "seconds": rows[-1]["t"]-rows[0]["t"] if len(rows) > 1 else 0,
            "note": "Isolated replay from an empty engine state; decisions near the start may differ. No camera commands."}
