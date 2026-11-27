import asyncio
import math

from confdeck.device_settings import writable


def propose_exclusions(session):
    labels = session.get("labels") or [r for r in session["rows"] if r.get("kind") == "label"]
    bins = {}
    for row in session["rows"]:
        if row.get("kind") != "mic" or not any(label["start"] <= row["t"] < label["end"] and label["condition"] in ("echo", "noise") for label in labels):
            continue
        event = row["event"]
        if event.get("beam_freeze") or (event.get("azimuth") == 315 and event.get("elevation") == 90):
            continue
        # Device coordinates are preserved independently of the application's orientation.
        a, e = event.get("device_angles", [event.get("azimuth"), event.get("elevation")])
        if type(a) not in (float, int) or type(e) not in (float, int) or not math.isfinite(a+e):
            continue
        key = (min(350, int(a//10)*10), min(80, int(e//10)*10))
        bins[key] = bins.get(key, 0)+1
    proposals = []
    for (a, e), count in sorted(bins.items(), key=lambda x: x[1], reverse=True)[:5]:
        if count < 5:
            continue
        proposals.append({"enabled": True, "azimuth": {"min": max(0, a-5), "max": min(360, a+15)},
                          "elevation": {"min": max(0, e-5), "max": min(90, e+15)}})
    return {"proposals": proposals, "requires_confirmation": True,
            "note": "Review against real seat positions; parked-beam signatures are not loudspeaker locations."}


async def settings_ab(device, profiles, run_trial, confirmed=False):
    """Operator-confirmed device experiment, restoring the immediate pre-experiment state."""
    if not confirmed or not 2 <= len(profiles) <= 3:
        raise ValueError("Confirm two or three setting sets before running A/B")
    before = await device.read()
    if before["unavailable"]:
        raise ValueError("Cannot snapshot all device controls")
    restore = {k: writable(v, device.resources[k]["schema"]) for k, v in before["values"].items()}
    scores = []
    try:
        for profile in profiles:
            await device.apply(profile, confirmed=True)
            scores.append(await run_trial(profile))
    finally:
        # Shield restoration against request cancellation; existing durable snapshot remains available.
        task = asyncio.create_task(device.apply(restore, confirmed=True))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    return {"scores": scores, "restored": True}
