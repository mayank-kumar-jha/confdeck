"""Reproducible protocol; labels are intervals, never inferred from predictions."""
import random

CONDITIONS = ("normal", "soft", "loud", "short", "alternation", "overlap",
              "silence", "noise", "echo", "moving", "transient", "long_pause")
QUIET = {"silence", "noise", "echo", "transient", "long_pause"}


def protocol(seats, seed=7, duration=8, countdown=3):
    if not seats:
        raise ValueError("Configure at least one seat")
    trials = [(s, c) for s in seats for c in CONDITIONS]
    random.Random(seed).shuffle(trials)
    result, t = [], 0.0
    for seat, condition in trials:
        other = seats[(seats.index(seat) + 1) % len(seats)]
        t += countdown
        labels = [seat, other, seat] if condition == "alternation" else [seat]
        trial_duration = 2 if condition == "short" else 20 if condition == "long_pause" else duration
        for index, target in enumerate(labels):
            span = trial_duration / len(labels)
            result.append({"start": t, "end": t + span, "seat": None if condition in QUIET else target,
                           "condition": condition, "speakers": [seat, other] if condition == "overlap" else [],
                           "countdown": countdown if index == 0 else 0})
            t += span
    return result
