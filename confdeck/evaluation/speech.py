"""Offline-only speech-gate comparison on the SAME scalar session.

Rows: kind=speech_frame, t=audio clock, level=dBFS, probability=Silero P.
No PCM accepted or persisted. Live runtime never imports this module.
"""
import argparse
import json
from pathlib import Path

from confdeck.speech import GateConfig, SpeechGate, finite, passes_guard
from confdeck.tracking import TrackingSettings
from confdeck.tracking_math.engine import SphericalFilter

from .metrics import metrics
from .session import load, validate


def run(session, settings, enabled=False, config=None):
    validate(session)
    if any(z.mic_id != "primary" or z.microphone_profiles for z in settings.zones):
        raise ValueError("Speech evaluation currently requires a single microphone")
    tracker = SphericalFilter(settings, speech=enabled)
    gate = SpeechGate(config) if enabled else None
    rows = list(session["rows"])
    if enabled:
        rows = [{**r, "t": r["t"]+gate.config.offset} if r["kind"] == "speech_frame" else r for r in rows]
        rows.sort(key=lambda r: r["t"])
    points, commands, tick = [], [], 0.
    covered = events = 0

    def save(t, decision):
        if decision:
            commands.append({**decision, "mocked": True})
        points.append(dict(t=t, seat=tracker.current if tracker.current != "wide" else None,
                           confidence=tracker.confidence, reason=tracker.reason))

    end = max([r["t"] for r in rows]+[x["end"] for x in session.get("labels", [])]+[0])
    for row in rows:
        t = row["t"]
        while tick < t-1e-8:
            save(tick, tracker.tick(tick))
            tick = round(tick+.1, 8)
        if row["kind"] == "speech_frame" and gate:
            gate.push(t-gate.config.offset, row["level"], row["probability"])
        elif row["kind"] == "mic":
            event = {**row["event"], "sample_time": t}
            event.pop("speech", None)
            events += 1
            if gate:
                evidence = gate.evidence(t)
                if evidence is not None:
                    covered += 1
                    event["speech"] = evidence
            save(t, tracker.feed(event, t))
    while tick <= end+1e-8:
        save(tick, tracker.tick(tick))
        tick = round(tick+.1, 8)
    result = dict(engine="speech" if enabled else "level", points=points, commands=commands, end=end)
    report = metrics(session, result, settings)
    labels = session.get("labels") or [r for r in session["rows"] if r["kind"] == "label"]
    quiet = [x for x in labels if x["condition"] == "soft" and x["seat"] is not None]
    locked = sum(any(x["start"] <= p["t"] < x["end"] and p["seat"] == x["seat"] for p in points) for x in quiet)
    return dict(wrong_rate=report["wrong_rate"],
                false_triggers_per_10min=None if report["spurious_switches_per_min"] is None else report["spurious_switches_per_min"]*10,
                quiet_lock_rate=locked/len(quiet) if quiet else None,
                median_latency=report["switch_latency_median"],
                audio_coverage=covered/events if events else 0.)


def compare(session, settings, config=None):
    before, after = run(session, settings), run(session, settings, True, config)
    return dict(before=before, after=after,
                accepted=after["audio_coverage"] == 1 and passes_guard(before, after),
                note="Offline scalar replay only; no live settings changed. Synthetic probabilities do not establish acoustic accuracy.")


def merge_scalar_frames(session, paths):
    """Align an opt-in scalar-only VAD JSONL capture with one Accuracy Lab session."""
    origin = session.get("monotonic_start")
    if not finite(origin):
        raise ValueError("This Accuracy Lab session has no monotonic clock origin; record a new session")
    end = max([r.get("t", 0) for r in session.get("rows", [])]
              + [label.get("end", 0) for label in session.get("labels", [])] + [0])
    frames, bytes_read, dropped = [], 0, 0
    for path in paths:
        with Path(path).open(encoding="utf-8") as source:
            header = None
            capture_start = last_stamp = None
            summary_seen = False
            for line in source:
                bytes_read += len(line.encode("utf-8"))
                if bytes_read > 32_000_000:
                    raise ValueError("Speech scalar capture exceeds 32 MB")
                row = json.loads(line)
                kind = row.get("kind")
                if header is None:
                    if (kind != "speech_frames" or row.get("version") != 1
                            or row.get("clock") != "monotonic" or not finite(row.get("started_monotonic"))):
                        raise ValueError("Unsupported or invalid speech scalar capture header")
                    header = True
                    capture_start = row["started_monotonic"]
                elif kind == "speech_frame":
                    stamp, level, probability = row.get("time"), row.get("level"), row.get("probability")
                    if (not all(finite(v) for v in (stamp, level, probability))
                            or not -120 <= level <= 0 or not 0 <= probability <= 1
                            or stamp < capture_start or (last_stamp is not None and stamp <= last_stamp)):
                        raise ValueError("Invalid scalar speech frame")
                    last_stamp = stamp
                    frames.append((stamp, level, probability))
                    if len(frames) > 100_000:
                        raise ValueError("Too many speech frames; use a shorter session")
                elif kind == "summary":
                    value = row.get("dropped")
                    if type(value) is not int or value < 0:
                        raise ValueError("Invalid speech capture summary")
                    dropped += value
                    summary_seen = True
                else:
                    raise ValueError("Unexpected record in speech scalar capture")
            if header is None or not summary_seen:
                raise ValueError("Incomplete speech scalar capture; stop it normally and repeat")
    if dropped:
        raise ValueError(f"Speech capture dropped {dropped} frames; repeat the session")
    if not frames:
        raise ValueError("Speech scalar capture contains no frames")
    ordered_frames = sorted(frames)
    if any(b[0] <= a[0] for a, b in zip(ordered_frames, ordered_frames[1:])):
        raise ValueError("Speech frame timestamps must be unique and ordered")
    # Keep only the window covered by the Accuracy Lab session. Starting the
    # capture a little later than the Lab is allowed; uncovered mic events fail
    # the strict audio-coverage guard in compare().
    merged = [*session.get("rows", [])]
    for stamp, level, probability in ordered_frames:
        t = stamp-origin
        if 0 <= t <= end:
            merged.append({"kind": "speech_frame", "t": t, "level": level, "probability": probability})
    if not any(row.get("kind") == "speech_frame" for row in merged):
        raise ValueError("Speech capture did not overlap the Accuracy Lab session; use the same mini PC and rerun")
    result = {**session, "rows": sorted(merged, key=lambda row: row.get("t", 0))}
    validate(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", nargs="+", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--offset", type=float, default=0)
    parser.add_argument("--scalar-frames", nargs="+", help="Scalar-only JSONL files from speech_audio --scalar-output")
    args = parser.parse_args()
    settings = TrackingSettings.model_validate_json(Path(args.settings).read_text(encoding="utf-8"))
    session = load(args.session)
    if args.scalar_frames:
        session = merge_scalar_frames(session, args.scalar_frames)
    print(json.dumps(compare(session, settings, GateConfig(offset=args.offset)), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
