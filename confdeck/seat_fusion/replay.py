"""Replay explicitly source-tagged scalar speech and beam events, without devices."""
import math

from confdeck.evaluation.session import validate
from confdeck.seat_fusion.engine import SeatFusion


def replay(session, settings, links=None, config=None, max_audio_age=.45, model=None):
    validate(session)
    if type(max_audio_age) not in (int, float) or not math.isfinite(max_audio_age) or max_audio_age <= 0:
        raise ValueError("Invalid maximum audio age")
    engine = SeatFusion(settings, links, config, model)
    frames, points, recommendations = {}, [], []
    next_tick = 0.

    def record(t, decision):
        if decision:
            recommendations.append({"zone": decision["seat"], "time": t, "shadow": True})
        # A held camera view is not fresh speaker evidence. Missing or
        # ambiguous input must count as no decision in the trial metric.
        detected = engine.current if engine.reason in ("same seat", "shadow recommendation") else None
        points.append({"t": t, "seat": detected, "held_view": engine.current, "reason": engine.reason,
                       "confidence": max(engine.scores.values(), default=0.)})

    rows = session.get("rows", [])
    end = max([r.get("t", 0) for r in rows] + [x["end"] for x in session.get("labels", [])] + [0])
    for row in rows:
        if "t" not in row:
            continue
        t = row["t"]
        while next_tick < t - 1e-8:
            record(next_tick, engine.tick(next_tick))
            next_tick = round(next_tick + .1, 8)
        if row.get("kind") == "speech_frame":
            source, reliability = row.get("mic_id"), row.get("reliability")
            if source not in engine.sources or type(reliability) not in (int, float) or not math.isfinite(reliability) or not 0 <= reliability <= 1:
                raise ValueError("Speech frames must have a configured mic_id and valid reliability")
            frames[source] = {"time": t, "reliability": reliability,
                              "no_signal": bool(row.get("no_signal")), "clipping": bool(row.get("clipping"))}
        elif row.get("kind") == "mic":
            event = {**row["event"], "sample_time": t}
            source = event.get("mic_id", "primary")
            event["mic_id"] = source
            frame = frames.get(source)
            if not frame or not 0 <= t-frame["time"] <= max_audio_age:
                frame = None
            # Ignore any shared speech field previously attached by the old tracker.
            event.pop("speech", None)
            record(t, engine.feed(event, frame, t))
    while next_tick <= end + 1e-8:
        record(next_tick, engine.tick(next_tick))
        next_tick = round(next_tick + .1, 8)
    return {"engine": "independent-audio-fusion-shadow", "points": points,
            "commands": recommendations, "end": end}


def trial_report(session, result, deadline=2.5, min_duration=1.5):
    """Count wrong and missed locks, and report no-speech switches separately."""
    validate(session)
    if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in (deadline, min_duration)):
        raise ValueError("Deadline and minimum duration must be positive")
    labels = session.get("labels") or [r for r in session.get("rows", []) if r.get("kind") == "label"]
    per_condition, total, passed = {}, 0, 0
    for label in labels:
        start, end, seat = label["start"], label["end"], label.get("seat")
        condition = label.get("condition", "unknown")
        if not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end)) or end <= start:
            raise ValueError("Invalid ground-truth interval")
        if seat is None or condition == "overlap" or end-start < min_duration:
            continue
        limit = min(end, start + deadline)
        hits = [p["t"] for p in result["points"] if start <= p["t"] <= limit and p["seat"] == seat]
        row = per_condition.setdefault(condition, {"trials": 0, "passed": 0})
        row["trials"] += 1
        total += 1
        if hits:
            row["passed"] += 1
            passed += 1
    quiet = [x for x in labels if x.get("seat") is None]
    false_switches = sum(1 for command in result["commands"]
                         if any(label["start"] <= command["time"] < label["end"] for label in quiet))
    quiet_seconds = sum(x["end"]-x["start"] for x in quiet)
    return {"eligible_trials": total, "passed_trials": passed,
            "trial_accuracy": passed/total if total else None,
            "deadline_seconds": deadline, "minimum_duration_seconds": min_duration,
            "per_condition": per_condition, "no_speech_switches": false_switches,
            "no_speech_seconds": quiet_seconds,
            "warning": "Shadow metrics require independently labeled room recordings; scores are not calibrated probabilities."}


def recorded_baseline(session):
    """Use the decisions actually recorded at the same time, not a different engine version."""
    validate(session)
    points, commands = [], []
    for row in session.get("rows", []):
        if row.get("kind") != "decision" or "t" not in row:
            continue
        t = row["t"]
        points.append({"t": t, "seat": row.get("seat"), "reason": row.get("reason", "")})
        decision = row.get("output")
        if isinstance(decision, dict) and decision.get("zone"):
            commands.append({"time": t, "zone": decision["zone"]})
    if not points:
        raise ValueError("The session has no recorded baseline decisions")
    return {"engine": "recorded-live-baseline", "points": points, "commands": commands}


def main():
    import argparse
    import json
    from pathlib import Path

    from confdeck.evaluation.session import load
    from confdeck.tracking import TrackingSettings

    parser = argparse.ArgumentParser(description="Compare camera-free shadow replay with recorded decisions")
    parser.add_argument("--session", nargs="+", required=True, help="All gzip JSONL session chunks")
    parser.add_argument("--settings", required=True, help="Tracking settings JSON from the same session")
    parser.add_argument("--links", help="Optional JSON mapping microphone-specific zone IDs to physical seat IDs")
    parser.add_argument("--fusion-model", help="Experimental model trained on separate calibration sessions")
    parser.add_argument("--output", required=True, help="New JSON result file")
    args = parser.parse_args()
    session = load(args.session)
    raw = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    settings = TrackingSettings.model_validate(raw.get("tracking", raw))
    links = json.loads(Path(args.links).read_text(encoding="utf-8")) if args.links else None
    model = None
    if args.fusion_model:
        from confdeck.seat_fusion.model import LearnedSeatModel, room_signature
        mapping = links or {}
        model = LearnedSeatModel.load(args.fusion_model,
            expected_sources={z.mic_id for z in settings.zones},
            expected_seats={mapping.get(z.id, z.id) for z in settings.zones},
            expected_signature=room_signature(settings, mapping))
    baseline = recorded_baseline(session)
    candidate = replay(session, settings, links, model=model)
    shadow_view = {**candidate, "points": [{**p, "seat": p.get("held_view")}
                                            for p in candidate["points"]]}
    report = {"baseline_held_view": trial_report(session, baseline),
              "shadow_held_view": trial_report(session, shadow_view),
              "shadow_speaker_detection": trial_report(session, candidate),
              "note": "Held-view metrics count a retained previous shot; detection treats missing or ambiguous evidence as no decision."}
    with Path(args.output).open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, allow_nan=False)
    print(f"Saved comparison to {args.output}")


if __name__ == "__main__":
    main()
