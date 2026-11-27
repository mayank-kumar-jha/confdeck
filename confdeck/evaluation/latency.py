"""Relative accuracy guard and bounded latency sweep, using camera-free replay."""
from itertools import product

from .metrics import metrics
from .replay import replay


def passes_guard(baseline, candidate):
    required = ("wrong_rate", "spurious_switches_per_min", "switch_latency_median")
    if any(baseline.get(k) is None or candidate.get(k) is None for k in required):
        return False
    return (candidate["wrong_rate"] <= baseline["wrong_rate"]+.005+1e-12
            and candidate["spurious_switches_per_min"] <= baseline["spurious_switches_per_min"]+1e-12
            and candidate["missed_trials"] <= baseline["missed_trials"])


def latency_sweep(session, settings):
    baseline = metrics(session, replay(session, settings, "new"), settings)
    accepted = []
    for floor, first, switch in product((250, 400, 600), (.78, .7, .85), (.9, .95)):
        inference = {**settings.inference.model_dump(), "evidence_floor_ms": floor, "p_on": first, "switch_p_on": switch}
        candidate = type(settings).model_validate({**settings.model_dump(), "engine": "new", "inference": inference})
        score = metrics(session, replay(session, candidate, "new"), candidate)
        if passes_guard(baseline, score):
            accepted.append({"profile": {"engine": "new", "inference": inference}, "score": score})
    accepted.sort(key=lambda r: (r["score"]["switch_latency_median"], r["score"]["wrong_rate"]))
    profiles = {}
    if accepted:
        profiles = {"Fast": accepted[0], "Balanced": accepted[len(accepted)//2], "Safe": min(accepted, key=lambda r: (r["score"]["wrong_rate"], -r["profile"]["inference"]["evidence_floor_ms"]))}
    return {"baseline": baseline, "evaluated": 18, "profiles": profiles, "suggested_profile": profiles.get("Fast", {}).get("profile"), "requires_confirmation": True, "note": "Session-specific guard; validate on held-out room sessions before live use."}
