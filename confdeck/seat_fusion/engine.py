"""Experimental shadow-only per-microphone fusion; never issues camera commands.

Scores and thresholds are uncalibrated starting values, not measured
probabilities. The source-specific VAD is the learned component; the fusion
is deliberately inspectable until sufficient labeled room data exist.
"""
import math
from dataclasses import dataclass

from confdeck.tracking_math.geometry import distance, vector


@dataclass(frozen=True)
class FusionConfig:
    max_age: float = .6
    min_score: float = .18
    min_margin: float = .12
    confirmation: float = .7

    def __post_init__(self):
        fields = (self.max_age, self.min_score, self.min_margin, self.confirmation)
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in fields):
            raise ValueError("Fusion settings must be finite")
        if not (.1 <= self.max_age <= 2 and 0 < self.min_score <= 1
                and 0 <= self.min_margin <= 1 and .1 <= self.confirmation <= 5):
            raise ValueError("Fusion settings outside allowed bounds")


class SeatFusion:
    """Consume only explicitly source-bound speech and direction in shadow mode.

    Optional links map multiple microphone-specific zone IDs to a canonical
    zone ID. These links require actual calibration and are never inferred from
    names, levels, or similar angles. Missing audio blocks new recommendations.
    """

    def __init__(self, settings, links=None, config=None, model=None):
        if any(z.microphone_profiles for z in settings.zones):
            raise ValueError('Experimental fusion does not support shared microphone profiles')
        self.settings = settings
        self.config = config or FusionConfig()
        zones = {z.id: z for z in settings.zones}
        self.links = dict(links or {})
        if any(source not in zones or target not in zones for source, target in self.links.items()):
            raise ValueError("Seat links must reference existing zone IDs")
        self.sources = sorted({z.mic_id for z in settings.zones})
        if len(self.sources) < 2:
            raise ValueError("Shadow fusion requires at least two calibrated microphones")
        self.zones = {source: [z for z in settings.zones if z.mic_id == source] for source in self.sources}
        self.model = model
        for source, entries in self.zones.items():
            targets = [self.links.get(z.id, z.id) for z in entries]
            if len(targets) != len(set(targets)):
                raise ValueError(f"More than one zone from {source} maps to the same physical seat")
        self.observations = {}
        self.last_stamps = {}
        self.current = None
        self.candidate = None
        self.candidate_since = None
        self.reason = "waiting for independent audio"
        self.scores = {}
        self.last_time = None

    def feed(self, event, speech, now):
        source = event.get("mic_id")
        if source not in self.zones:
            return None
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("Invalid event time")
        if self.last_time is not None and now < self.last_time:
            return None
        self.last_time = now
        stamp = event.get("sample_time")
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            self.observations.pop(source, None)
            return self._decide(now)
        if stamp <= self.last_stamps.get(source, -math.inf):
            return None
        if not 0 <= now-stamp <= self.config.max_age:
            self.observations.pop(source, None)
            return self._decide(now)
        self.last_stamps[source] = stamp
        azimuth, elevation = event.get("azimuth"), event.get("elevation")
        valid = (all(type(v) in (int, float) and math.isfinite(v) for v in (azimuth, elevation))
                 and 0 <= azimuth <= 360 and 0 <= elevation <= 90
                 and not event.get("beam_freeze") and not event.get("overlap")
                 and isinstance(speech, dict) and not speech.get("unavailable")
                 and not speech.get("clipping") and not speech.get("no_signal"))
        reliability = speech.get("reliability") if isinstance(speech, dict) else None
        valid = valid and type(reliability) in (int, float) and math.isfinite(reliability) and 0 <= reliability <= 1
        if not valid:
            self.observations.pop(source, None)
            return self._decide(now)
        beam = vector(azimuth, elevation, self.settings.inference.convention)
        scores = {}
        for zone in self.zones[source]:
            centers = ([c.mean for c in zone.components] if zone.components
                       else [vector(zone.azimuth, zone.elevation, self.settings.inference.convention)])
            angular_error = min(distance(beam, center) for center in centers)
            # No spatial support outside the zone's calibrated angular radius.
            if angular_error <= zone.radius:
                value = reliability * math.exp(-.5 * (angular_error / zone.radius)**2)
                target = self.links.get(zone.id, zone.id)
                scores[target] = max(scores.get(target, 0.), value)
        sample = None
        if self.model:
            from confdeck.seat_fusion.model import features
            sample = features(event, speech, self.settings.inference.convention)
            if sample is None:
                self.observations.pop(source, None)
                return self._decide(now)
        self.observations[source] = (now, scores, sample)
        return self._decide(now)

    def tick(self, now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("Invalid tick time")
        if self.last_time is not None and now < self.last_time:
            return None
        self.last_time = now
        return self._decide(now)

    def _decide(self, now):
        if any(source not in self.observations or not 0 <= now-self.observations[source][0] <= self.config.max_age
               for source in self.sources):
            self.candidate = self.candidate_since = None
            self.scores = {}
            self.reason = "independent audio or direction missing / keep previous view"
            return None
        scores = {}
        none_score = 0.
        if self.model:
            from confdeck.seat_fusion.model import NO_SPEAKER
            if not any(self.observations[source][2][-1] > 0 for source in self.sources):
                self.candidate = self.candidate_since = None
                self.scores = {}
                self.reason = "no qualified speaker / keep previous view"
                return None
            predicted = self.model.predict({source: self.observations[source][2] for source in self.sources})
            none_score = predicted.pop(NO_SPEAKER)
            scores.update(predicted)
        else:
            for _, values, _ in self.observations.values():
                for seat, value in values.items():
                    scores[seat] = max(scores.get(seat, 0.), value)
        self.scores = scores
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < self.config.min_score:
            self.candidate = self.candidate_since = None
            self.reason = "no qualified speaker / keep previous view"
            return None
        seat, score = ranked[0]
        runner = max(none_score, ranked[1][1] if len(ranked) > 1 else 0.)
        if score-runner < self.config.min_margin:
            self.candidate = self.candidate_since = None
            self.reason = "ambiguous seats / keep previous view"
            return None
        if seat == self.current:
            self.candidate = self.candidate_since = None
            self.reason = "same seat"
            return None
        if seat != self.candidate:
            self.candidate, self.candidate_since = seat, now
        if now-self.candidate_since + 1e-9 < self.config.confirmation:
            self.reason = "confirming seat"
            return None
        self.current, self.candidate, self.candidate_since = seat, None, None
        self.reason = "shadow recommendation"
        return {"seat": seat, "time": now, "score": score, "shadow": True}

    def snapshot(self, now):
        return {"mode": "shadow", "scorer": "trained" if self.model else "uncalibrated",
                "current": self.current, "candidate": self.candidate,
                "reason": self.reason, "scores": dict(self.scores),
                "sources": {s: (s in self.observations and 0 <= now-self.observations[s][0] <= self.config.max_age)
                            for s in self.sources}}
