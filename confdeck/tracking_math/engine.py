"""O(N*3) spherical HMM with inherited camera movement safeguards."""
import math
from collections import deque

import numpy as np

from confdeck.tracking import TrackingFilter, delta

from .model import SeatModel
from .quality import QualityGate


class SphericalFilter(TrackingFilter):
    def __init__(self, settings, speech=False):
        super().__init__(settings)
        self.cfg = settings.inference
        self.model = SeatModel(settings.zones, self.cfg.convention)
        self.gate = QualityGate(settings, speech=speech)
        self.posterior = np.zeros(len(settings.zones)+1)
        self.posterior[-1] = 1
        self.confidence = 0.
        self.infer_time = None
        self.below_since = None
        self.pre_roll = deque(maxlen=80)

    def cooldown(self, now):
        # The motion rate limit still applies; silence does not impose a seat-switch hold.
        return super().cooldown(now) if self.current in self.model.ids else 0

    def advance(self, now):
        dt = max(0, now-self.infer_time) if self.infer_time is not None else .1
        self.infer_time = now
        p = 1-(1-self.cfg.switch_probability)**(dt/.1)
        self.posterior = (1-p)*self.posterior + p/len(self.posterior)
        if self.last_voice is None or now-self.last_voice > .4:
            decay = math.exp(-dt*4)
            self.posterior[:-1] *= decay
            self.posterior[-1] = 1-self.posterior[:-1].sum()

    def tick(self, now):
        self.advance(now)
        if self.current in self.model.ids:
            value = self.posterior[self.model.ids.index(self.current)]
            if value < self.cfg.p_off:
                if self.below_since is None:
                    self.below_since = now
                if now-self.below_since >= self.cfg.release_ms/1000:
                    self.current = None
                    self.confidence = 0
                    self.reason = "released to none"
            else:
                self.below_since = None
        return super().tick(now)

    def _feed(self, event, now):
        if event.get("mic_id", "primary") not in self.microphone_ids:
            return self.reject("microphone has no calibrated seats")
        if self.infer_time is not None and now < self.infer_time:
            return self.reject("out-of-order event")
        stamp = event.get("sample_time", now)
        if (type(stamp) in (int, float) and math.isfinite(stamp)
                and self.gate.last_time is not None and stamp <= self.gate.last_time):
            return self.reject("ignored: duplicate or old sample")
        self.advance(now)
        reliability, reason, v = self.gate.feed(event, now)
        self.reason = reason
        raw = [event.get("azimuth"), event.get("elevation")]
        raw = [value if type(value) in (int, float) and math.isfinite(value) else 0 for value in raw]
        self.history.append({"time": now, "raw": raw,
                             "smooth": self.smoothed or raw, "accepted": reliability > 0, "reason": reason})
        while self.pre_roll and now-self.pre_roll[0][0] > .4:
            self.pre_roll.popleft()
        if not reliability or not self.model.ids:
            if v is not None and reason in ("below noise floor", "short spike / awaiting sustained energy", "awaiting speech"):
                self.pre_roll.append((now, v))
            else:
                self.pre_roll.clear()
            self.candidate = None
            self.posterior[:-1] *= .7
            self.posterior[-1] = 1-self.posterior[:-1].sum()
            return None
        self.last_sample = self.last_voice = now
        # Only score recent, valid directions corroborated by the newly opened gate.
        # They can seed the posterior, but cannot independently commit a seat.
        from .geometry import distance
        for _, prior in self.pre_roll:
            if distance(prior, v) <= 25:
                scores = self.model.likelihood(prior)
                self.posterior *= np.append(np.maximum(scores, 1e-12)**(.15*reliability if self.gate.speech else .15), 1.)
                self.posterior /= self.posterior.sum()
        self.pre_roll.clear()
        likelihood = self.model.likelihood(v)
        evidence = np.log(np.maximum(likelihood, 1e-12))*reliability
        for i, seat in enumerate(self.model.ids):
            llr = event.get("vision_llr", {}).get(seat, 0)
            if type(llr) in (int, float) and math.isfinite(llr):
                clipped = float(np.clip(min(0, llr) if self.cfg.vision_veto_only else llr, -4, 4))
                if clipped <= -3.5 and self.cfg.vision_weight > 0:
                    # Confirmed empty seat must veto acoustic attraction even at default vision_weight.
                    evidence[i] = min(float(evidence[i]), 0.0) + max(self.cfg.vision_weight, 0.75) * clipped
                else:
                    evidence[i] += self.cfg.vision_weight * clipped
        emissions = np.append(np.exp(np.clip(evidence, -20, 20)), 1.)
        self.posterior *= emissions
        self.posterior /= self.posterior.sum()
        best = int(np.argmax(self.posterior))
        self.confidence = float(self.posterior[best])
        if best == len(self.model.ids):
            self.candidate = None
            return self.reject("none / clutter")
        zone = self.settings.zones[best]
        runner = float(np.partition(self.posterior, -2)[-2])
        barrier = self.cfg.switch_p_on if self.current in self.model.ids else self.cfg.p_on
        if event.get("overlap") or self.confidence < barrier or self.confidence-runner < self.cfg.margin:
            self.candidate = None
            return self.reject("split posterior / keep previous seat")
        # Display smoothing only within the winning component, never across modes.
        from .geometry import angles
        cs = self.model.components[best]
        winner = max(cs, key=lambda c: float(np.dot(c["mean"], v)))
        self.smoothed = angles(winner["mean"], self.cfg.convention)
        if zone.id == self.current:
            self.candidate = None
            return self.reject("accepted: same seat")
        if self.candidate != zone.id:
            self.candidate, self.candidate_since = zone.id, now
        old = next((z for z in self.settings.zones if z.id == self.current), None)
        multiplier = self.cfg.opposite_multiplier if old and abs(delta(old.azimuth, zone.azimuth)) >= 140 else 1
        if now-self.candidate_since+1e-9 < self.cfg.evidence_floor_ms/1000*multiplier:
            return self.reject("confirming opposite candidate" if multiplier > 1 else "confirming candidate")
        if self.ready(now, len(zone.presets)):
            evidence_start = min(self.candidate_since, self.gate.energy_since if self.current not in self.model.ids and self.gate.energy_since is not None else self.candidate_since)
            decision = self.commit(zone.id, zone.presets, now)
            return {**decision, "seat": zone.id, "confidence": self.confidence, "reason": self.reason, "evidence_start": evidence_start}
        return None

    def snapshot(self, now):
        return {**super().snapshot(now), "engine": "new", "confidence": self.confidence,
                "posteriors": dict(zip(self.model.ids+["none"], self.posterior.tolist())),
                "noise_floor": self.gate.noise_floor, "rejected": dict(self.gate.rejected),
                "quality_reason": self.gate.last_reason}
