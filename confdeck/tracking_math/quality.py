import math
from collections import Counter, deque

import numpy as np

from .geometry import distance, vector


class QualityGate:
    def __init__(self, settings, speech=False):
        self.s, self.cfg = settings, settings.inference
        self.speech = speech  # Offline evaluator opt-in; live callers remain unchanged.
        self.levels = deque(maxlen=300)
        self.noise_floor = -75.
        self.floor_time = -math.inf
        self.energy_since = self.last_time = self.previous = self.same_since = None
        self.same_levels = deque(maxlen=60)
        self.jump_count = 0
        self.jump_vector = None
        self.rejected = Counter()
        self.last_reason = "waiting"

    def result(self, reliability, reason, v=None):
        self.last_reason = reason
        if reliability < 1:
            self.rejected[reason] += 1
        return reliability, reason, v

    def feed(self, event, now):
        c = self.cfg
        a, e, level = (event.get(k) for k in ("azimuth", "elevation", "level"))
        stamp = event.get("sample_time", now)
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, e, stamp)) or not 0 <= a <= 360 or not self.s.elevation_min <= e <= self.s.elevation_max:
            return self.result(0, "invalid angles or timestamp")
        if self.last_time is not None and stamp <= self.last_time:
            return self.result(0, "duplicate or old sample")
        if self.last_time is not None and stamp-self.last_time > .6:
            self.energy_since = None
        self.last_time = stamp
        v = vector(a, e, c.convention)
        if event.get("beam_freeze") or (c.parked_enabled and abs((a-c.parked_azimuth+180)%360-180) <= c.parked_tolerance and abs(e-c.parked_elevation) <= c.parked_tolerance):
            self.energy_since = None
            return self.result(0, "far-end / parked beam", v)
        if type(level) not in (float, int) or not math.isfinite(level) or not -90 <= level <= 0:
            self.energy_since = None
            return self.result(0, "level unavailable", v)
        speech = event.get("speech") if self.speech else None
        # Update the floor only from known non-speech, or low-level samples when
        # activity is unavailable. Do not learn sustained speech as room noise.
        known_quiet = (event.get("activity") is False or
                       isinstance(speech, dict) and speech.get("reliability") == 0 or
                       event.get("activity") is None and speech is None and level < self.s.level_threshold)
        if known_quiet:
            self.levels.append((now, level))
        while self.levels and now-self.levels[0][0] > 30:
            self.levels.popleft()
        if not self.levels:
            self.noise_floor = -75.
        if len(self.levels) >= 5 and now-self.floor_time >= 1:
            low = float(np.percentile([x[1] for x in self.levels], 10))
            self.noise_floor = low
            self.floor_time = now
        weight = 1.
        if speech is not None:
            weight = speech.get("reliability", 0)
            if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1:
                return self.result(0, "invalid speech evidence", v)
            if event.get("activity") is False or weight == 0:
                self.energy_since = None
                return self.result(0, "awaiting speech", v)
        elif event.get("activity") is False or level < max(self.s.level_threshold, self.noise_floor+c.noise_margin):
            self.energy_since = None
            return self.result(0, "below noise floor", v)
        if self.s.activity_gate and event.get("activity") is not True:
            return self.result(0, "activity unavailable", v)
        if self.energy_since is None:
            self.energy_since = now
        previous = self.previous
        self.previous = (a, e, now, v)
        if previous and (a, e) == previous[:2]:
            if self.same_since is None:
                self.same_since = previous[2]
                self.same_levels.clear()
            self.same_levels.append(level)
            if now-self.same_since >= c.frozen_seconds:
                speech_active = speech is not None and weight > 0
                flat_energy = len(self.same_levels) < 5 or (max(self.same_levels) - min(self.same_levels)) < 2.5
                if not speech_active and flat_energy:
                    return self.result(0, "frozen high-level beam", v)
        else:
            self.same_since = None
            self.same_levels.clear()
        if speech is None and now-self.energy_since+1e-9 < c.energy_ms/1000:
            return self.result(0, "short spike / awaiting sustained energy", v)
        if self.jump_vector is not None:
            if distance(self.jump_vector, v) < 25:
                self.jump_count += 1
                if self.jump_count < 3:
                    return self.result(.15*weight, "corroborating jump", v)
                self.jump_vector, self.jump_count = None, 0
            else:
                self.jump_vector, self.jump_count = None, 0
        if previous and now-previous[2] < .6 and distance(previous[3], v) > c.jump_degrees:
            if self.jump_vector is not None and distance(self.jump_vector, v) < 25:
                self.jump_count += 1
            else:
                self.jump_vector, self.jump_count = v, 1
            return self.result((.15 if self.jump_count < 3 else 1)*weight, "implausible jump" if self.jump_count < 3 else "accepted", v)
        return self.result(weight, "accepted", v)
