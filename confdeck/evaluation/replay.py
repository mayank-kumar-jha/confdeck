"""Event-time simulation with fixed ticks; no sleeping or camera access."""
from confdeck.tracking import TrackingFilter

from .session import validate


def make_engine(settings, engine="legacy", speech=False):
    if any(z.mic_id != "primary" or z.microphone_profiles for z in settings.zones):
        from confdeck.multimic import MultiMicFilter
        return MultiMicFilter(settings, engine, speech=speech)
    if engine == "legacy":
        return TrackingFilter(settings)
    if engine == "new":
        from confdeck.tracking_math.engine import SphericalFilter
        return SphericalFilter(settings, speech=speech)
    raise ValueError("Engine must be legacy or new")


def replay(session, settings, engine="legacy", vision="off"):
    validate(session)
    shared = any(zone.microphone_profiles for zone in settings.zones)
    tracker = make_engine(settings, engine, speech=shared and engine == 'new')
    points, commands, next_tick = [], [], 0.0
    vision_scores, vision_time = {}, -100.

    def save(t, decision):
        if decision:
            commands.append({**decision, "mocked": True})
        points.append({"t": t, "seat": tracker.current if tracker.current != "wide" else None,
                       "confidence": float(getattr(tracker, "confidence", 0)), "reason": tracker.reason})

    rows = session["rows"]
    end = max([r.get("t", 0) for r in rows] + [x["end"] for x in session.get("labels", [])] + [0])
    for row in rows:
        if "t" not in row:
            continue
        t = row["t"]
        while next_tick < t - 1e-8:
            save(next_tick, tracker.tick(next_tick))
            next_tick = round(next_tick + .1, 8)
        if row["kind"] == "vision":
            vision_scores = row.get("scores", {})
            vision_time = t
        if row["kind"] == "mic":
            event = {**row["event"], "time": t, "sample_time": t}
            if shared and engine == 'new' and not isinstance(event.get('speech'), dict):
                event['speech'] = {'reliability': 0}
            if vision == "active" and t-vision_time < .6:
                event["vision_llr"] = vision_scores
            save(t, tracker.feed(event, t))
    while next_tick <= end + 1e-8:
        save(next_tick, tracker.tick(next_tick))
        next_tick = round(next_tick + .1, 8)
    return {"engine": engine, "vision": vision, "points": points, "commands": commands, "end": end}
