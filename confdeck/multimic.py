"""Independent angular spaces with bounded, conservative speech arbitration."""

import math

from confdeck.tracking import TrackingFilter
from confdeck.tracking_math.geometry import distance, vector


class MultiMicFilter(TrackingFilter):
    """Confirm seats per microphone; enforce hold/rate limits once for the room.

    Angles, smoothing and noise floors must never be shared across ceiling mics.
    Heterogeneous ceiling mics (e.g. TCC2 vs TCCM) have different hardware preamp
    gains, so arbitration uses a composite score combining SNR, posterior confidence,
    and angular proximity to the seat center rather than raw audio level.
    Only fresh, qualified samples can nominate a seat, never a retained lock.
    """

    def __init__(self, settings, engine, speech=False):
        super().__init__(settings)
        from confdeck.tracking_math.engine import SphericalFilter
        self.speech = speech
        factory = (lambda cfg: SphericalFilter(cfg, speech=speech)) if engine == "new" else TrackingFilter
        self.sources = {}
        self.votes = {}
        self.active_mic = None
        self.confidence = 0.
        self.nomination = None
        self.nomination_since = 0.
        for source in dict.fromkeys(z.mic_id for z in settings.zones):
            cfg = settings.model_copy(deep=True)
            cfg.zones = [z for z in cfg.zones if z.mic_id == source]
            cfg.wide_presets = {}
            cfg.conversation_layout = False
            # Local engines confirm evidence. The room owns all movement budgets.
            cfg.max_moves_per_min = 60
            cfg.min_hold_ms = 500
            cfg.settle_ms = 0
            cfg.level_gate = True
            self.sources[source] = factory(cfg)

    def feed(self, event, now):
        source = event.get("mic_id", "primary")
        tracker = self.sources.get(source)
        if tracker is None:
            return self.reject("microphone has no calibrated seats")
        result = tracker.feed(event, now)
        level = event.get("level")
        fresh = (tracker.last_sample == now and type(level) in (int, float)
                 and math.isfinite(level) and -90 <= level <= 0
                 and not event.get("overlap"))
        confirmed = result is not None or tracker.reason in (
            "accepted: same seat", "ignored: same zone")
        if fresh:
            self.last_sample = self.last_voice = now
        if not fresh or not confirmed:
            if self.nomination and self.nomination[0] == source:
                self.nomination = None
            self.votes.pop(source, None)
            self.reason = tracker.reason
            return None

        gate = getattr(tracker, "gate", None)
        nf = getattr(gate, "noise_floor", -75.0) if gate else -75.0
        snr = max(0.0, level - nf)
        conf = getattr(tracker, "confidence", 1.0)
        if not isinstance(conf, (int, float)) or not math.isfinite(conf):
            conf = 1.0
        seat = tracker.current

        # Use the measured beam, not tracker.smoothed: the spherical tracker
        # exposes the winning model center as its display position.
        zone = next((z for z in self.settings.zones if z.id == seat), None) if seat else None
        prox = 0.0
        azimuth, elevation = event.get("azimuth"), event.get("elevation")
        if (zone and type(azimuth) in (int, float) and math.isfinite(azimuth)
                and type(elevation) in (int, float) and math.isfinite(elevation)):
            convention = self.settings.inference.convention
            beam = vector(azimuth, elevation, convention)
            centers = ([c.mean for c in zone.components] if zone.components
                       else [vector(zone.azimuth, zone.elevation, convention)])
            ang_dist = min(distance(beam, center) for center in centers)
            prox = max(0.0, 1.0 - ang_dist / max(1.0, zone.radius))

        # Composite score balancing SNR, posterior confidence, and angular proximity.
        # This prevents a mic with a hotter pre-amp from stealing a seat when another
        # mic's beam is directly locked on the physical speaker.
        score = snr + 15.0 * conf + 15.0 * prox

        # Store comprehensive vote: (timestamp, level, snr, seat, score, conf, prox)
        self.votes[source] = (now, level, snr, seat, score, conf, prox)
        votes = {k: v for k, v in self.votes.items() if 0 <= now - v[0] <= 0.6}

        # Check if the currently locked seat has an active incumbent microphone
        incumbent_zone = next((z for z in self.settings.zones if z.id == self.current), None) if self.current and self.current != "wide" else None
        incumbent_mic = incumbent_zone.mic_id if incumbent_zone else None
        incumbent_active = incumbent_mic in votes and (now - votes[incumbent_mic][0] <= 0.6)

        # Winner chosen by composite score rather than raw preamp level
        winner = max(votes, key=lambda k: votes[k][4])
        _, strength, winner_snr, seat, winner_score, winner_conf, winner_prox = votes[winner]

        # Incumbent protection: If a seat is already active and the incumbent mic is speaking,
        # require a challenger from another mic to exceed incumbent by 6 score points while active,
        # decaying to 3.5 points during natural speech pauses (> 250ms) or poor incumbent lock.
        if incumbent_active and winner != incumbent_mic:
            inc_time, inc_level, inc_snr, inc_seat, inc_score, inc_conf, inc_prox = votes[incumbent_mic]
            inc_age = now - inc_time
            required_margin = 3.5 if (inc_age >= 0.25 or inc_snr < 10.0 or inc_prox < 0.4) else 6.0
            challenger_margin = winner_score - inc_score
            if challenger_margin < required_margin:
                winner = incumbent_mic
                _, strength, winner_snr, seat, winner_score, winner_conf, winner_prox = votes[winner]

        if not seat:
            return None
        zone = next((z for z in self.settings.zones if z.id == seat), None)
        if not zone:
            return None

        for other, (_, other_level, other_snr, other_seat, other_score, _, _) in votes.items():
            if not other_seat:
                continue
            other_zone = next((z for z in self.settings.zones if z.id == other_seat), None)
            if not other_zone:
                continue
            same_scene = bool(zone.presets) and zone.presets == other_zone.presets
            if other != winner and not same_scene:
                score_diff = winner_score - other_score
                if not self.current and score_diff < 3.0:
                    self.nomination = None
                    return self.reject("microphones disagree / keep previous seat", True)
                if score_diff < 4.0 and self.current:
                    if seat != self.current:
                        self.nomination = None
                        return self.reject("microphones disagree / keep previous seat", True)

        # Never dispatch using a cached vote while processing a different source.
        if winner != source:
            return self.reject("stronger microphone has priority")
        self.active_mic = winner
        self.smoothed = tracker.smoothed
        self.confidence = getattr(tracker, "confidence", 1.)
        self.candidate_since = tracker.candidate_since
        if seat == self.current:
            self.nomination = None
            return self.reject("accepted: same seat", True)
        self.candidate = seat
        if self.nomination != (winner, seat):
            self.nomination = (winner, seat)
            self.nomination_since = now
        if now - self.nomination_since < .2:
            return self.reject("confirming microphone priority")
        previous = next((z for z in self.settings.zones if z.id == self.current), None)
        if previous and previous.presets == zone.presets:
            self.current, self.candidate = seat, None
            return self.reject("same camera framing / microphone changed")
        if zone.presets and self.ready(now, len(zone.presets)):
            return {**self.commit(seat, zone.presets, now), "mic_id": source,
                    "evidence_start": self.candidate_since}
        return None

    def tick(self, now):
        for tracker in self.sources.values():
            tracker.tick(now)
        return super().tick(now)

    def snapshot(self, now):
        sources = {k: v.snapshot(now) for k, v in self.sources.items()}
        for view in sources.values():
            view["history"] = view["history"][-40:]
        return {**super().snapshot(now), "active_mic": self.active_mic,
                "confidence": self.confidence,
                "sources": sources}
