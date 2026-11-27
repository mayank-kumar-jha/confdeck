"""Bounded grid suggestions; never applies a profile or writes device settings."""
from itertools import product

from .metrics import metrics
from .replay import replay


def sweep(session, settings, grid, limit=0.3, engine="new"):
    if not grid or set(grid) - set(type(settings).model_fields):
        raise ValueError("Choose valid settings fields")
    count = 1
    for values in grid.values():
        count *= len(values)
    if count > 128:
        raise ValueError("Sweep limited to 128 profiles")
    candidates = []
    for values in product(*grid.values()):
        patch = dict(zip(grid, values))
        candidate = type(settings).model_validate({**settings.model_dump(), **patch})
        score = metrics(session, replay(session, candidate, engine), candidate)
        if score["spurious_switches_per_min"] is not None and score["spurious_switches_per_min"] <= limit and score["correct_rate"] is not None:
            candidates.append((score["correct_rate"], patch, score))
    best = max(candidates, key=lambda x: x[0]) if candidates else None
    return {"requires_confirmation": True, "suggested_profile": best[1] if best else None,
            "score": best[2] if best else None, "evaluated": count,
            "note": "Validate this suggestion on a separate held-out test session before applying."}
