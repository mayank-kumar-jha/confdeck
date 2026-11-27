"""Movement-budgeted seat decisions for the existing OmniTrack camera bridge."""
import math

from confdeck.tracking import TrackingFilter


class FusionController(TrackingFilter):
    """Keep seat changes separate from inference and never issue camera commands.

    The existing bridge reads current from the tracking snapshot. A candidate
    must remain fresh and unambiguous while camera hold and rate limits apply.
    """

    def __init__(self, settings):
        super().__init__(settings)
        self.pending = None

    def observe(self, fusion, now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("Invalid decision time")
        state = fusion.snapshot(now)
        if (state.get("scorer") != "trained" or not state.get("sources")
                or not all(state["sources"].values())
                or state.get("reason") not in ("same seat", "shadow recommendation")):
            self.pending = None
            self.reason = "insufficient fresh evidence / keep previous view"
            return None
        seat = state.get("current")
        zone = next((z for z in self.settings.zones if z.id == seat), None)
        if not zone or not zone.presets:
            self.pending = None
            self.reason = "no configured camera framing / keep previous view"
            return None
        self.last_sample = self.last_voice = now
        if seat == self.current:
            self.pending = None
            self.reason = "accepted: same seat"
            return None
        self.pending = seat
        if not self.ready(now, len(zone.presets)):
            return None
        self.pending = None
        return self.commit(seat, zone.presets, now)
