"""Deterministic voice tracking. Time is injected so recordings and live input agree."""

import math
import re
from collections import deque
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from confdeck.tracking_math.config import Component, InferenceSettings, VisionSettings


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class MicrophoneProfile(Model):
    azimuth: float = Field(ge=0, lt=360)
    elevation: float = Field(ge=0, le=90)
    radius: float = Field(default=25, ge=5, le=90)
    components: list[Component] = Field(default_factory=list, max_length=3)


class Zone(Model):
    mic_id: str = Field(default="primary", pattern=r"^[a-z0-9_-]{1,40}$")
    components: list[Component] = Field(default_factory=list, max_length=3)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    azimuth: float = Field(ge=0, lt=360)
    elevation: float = Field(ge=0, le=90)
    radius: float = Field(default=25, ge=5, le=90)
    azimuth_range: tuple[float, float] | None = None
    elevation_range: tuple[float, float] | None = None
    scene_id: str = Field(default="", max_length=40)
    presets: dict[str, str] = Field(default_factory=dict, max_length=16)
    microphone_profiles: dict[str, MicrophoneProfile] = Field(default_factory=dict, max_length=7)

    @model_validator(mode="after")
    def ranges(self):
        if self.mic_id in self.microphone_profiles or any(
                not re.fullmatch(r'[a-z0-9_-]{1,40}', source)
                for source in self.microphone_profiles):
            raise ValueError('Additional microphone profiles need distinct valid microphone IDs')
        if (self.azimuth_range is None) != (self.elevation_range is None):
            raise ValueError("Provide both azimuth and elevation ranges")
        if self.azimuth_range is not None:
            if any(not 0 <= x <= 360 for x in self.azimuth_range) or not 0 <= self.elevation_range[0] < self.elevation_range[1] <= 90:
                raise ValueError("Invalid coordinate ranges")
            if not zone_contains(self, (self.azimuth, self.elevation)):
                raise ValueError("Zone center must be inside its ranges")
        return self


class TrackingSettings(Model):
    engine: Literal["legacy", "new"] = "legacy"
    conversation_layout: bool = False
    conversation_hold_ms: int = Field(default=6000, ge=1000, le=15000)
    presenter_seat: str = Field(default="", max_length=40)
    presenter_camera: str = Field(default="", max_length=40)
    inference: InferenceSettings = Field(default_factory=InferenceSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    profile: Literal["Calm", "Balanced", "Responsive", "Custom"] = "Balanced"
    confirm_ms: int = Field(default=1000, ge=100, le=10000)
    min_hold_ms: int = Field(default=3000, ge=500, le=30000)
    deadband_deg: float = Field(default=8, ge=0, le=45)
    zone_margin_deg: float = Field(default=8, ge=0, le=30)
    ema_alpha: float = Field(default=.25, ge=.01, le=1)
    median_n: int = Field(default=1, ge=1, le=15)
    elevation_min: float = Field(default=0, ge=0, le=90)
    elevation_max: float = Field(default=90, ge=0, le=90)
    activity_gate: bool = False
    level_gate: bool = False
    level_threshold: float = Field(default=-55, ge=-90, le=0)
    max_moves_per_min: int = Field(default=12, ge=1, le=60)
    settle_ms: int = Field(default=1000, ge=0, le=10000)
    silence_ms: int = Field(default=10000, ge=1000, le=120000)
    preset_speed: int | None = Field(default=None, ge=1, le=100)
    zones: list[Zone] = Field(default_factory=list, max_length=64)
    wide_presets: dict[str, str] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def coherent(self):
        if self.elevation_min > self.elevation_max:
            raise ValueError("Minimum elevation must not exceed maximum elevation.")
        if len({z.id for z in self.zones}) != len(self.zones):
            raise ValueError("Zone identifiers must be unique.")
        if any(z.radius <= self.zone_margin_deg or any(p.radius <= self.zone_margin_deg
               for p in z.microphone_profiles.values()) for z in self.zones):
            raise ValueError("Each zone radius must be larger than the zone margin.")
        for mapping in [self.wide_presets, *(z.presets for z in self.zones)]:
            if any(not k or len(k) > 40 or not v or len(v) > 128 for k, v in mapping.items()):
                raise ValueError("Choose a camera and a valid preset for every assignment.")
        return self


PROFILES = {
    "Calm": dict(confirm_ms=1500, min_hold_ms=5000, deadband_deg=12, zone_margin_deg=10, ema_alpha=.15),
    "Balanced": dict(confirm_ms=1000, min_hold_ms=3000, deadband_deg=8, zone_margin_deg=8, ema_alpha=.25),
    "Responsive": dict(confirm_ms=600, min_hold_ms=1500, deadband_deg=5, zone_margin_deg=5, ema_alpha=.4),
}


def delta(a, b):
    return (a - b + 180) % 360 - 180


def distance(a, b):
    return math.hypot(delta(a[0], b[0]), a[1] - b[1])


def zone_contains(zone, point, margin=0):
    """Complete the existing rectangular-zone contract; legacy circles are unchanged."""
    if zone.azimuth_range is None:
        return distance(point, (zone.azimuth, zone.elevation)) <= zone.radius-margin
    lo, hi = zone.azimuth_range
    span = (hi-lo) % 360 if hi-lo != 360 else 360
    offset = (point[0]-lo) % 360
    return margin <= offset <= span-margin and zone.elevation_range[0]+margin <= point[1] <= zone.elevation_range[1]-margin


def average(points):
    x = sum(math.cos(math.radians(p[0])) for p in points)
    y = sum(math.sin(math.radians(p[0])) for p in points)
    if math.hypot(x, y) < len(points) * .2:
        raise ValueError("The direction was not stable. Speak from one seat and try again.")
    return math.degrees(math.atan2(y, x)) % 360, sum(p[1] for p in points) / len(points)


class TrackingFilter:
    def __init__(self, settings=None):
        self.settings = settings or TrackingSettings()
        self.microphone_ids = {z.mic_id for z in self.settings.zones} or {"primary"}
        self.smoothed = None
        self.window = deque(maxlen=self.settings.median_n)
        self.current = None
        self.candidate = None
        self.candidate_since = 0
        self.uncertain_since = None
        self.last_sample = None
        self.last_voice = None
        self.last_time = None
        self.last_event_time = None
        self.last_move = -math.inf
        self.last_target = None
        self.moves = deque()
        self.reason = "Waiting for microphone"
        self.history = deque(maxlen=1200)

    def reject(self, reason, clear=False):
        self.reason = reason
        if clear:
            self.candidate = None
        return None

    def cooldown(self, now):
        return max(0, self.last_move + max(self.settings.min_hold_ms, self.settings.settle_ms) / 1000 - now)

    def ready(self, now, count):
        while self.moves and now - self.moves[0] >= 60:
            self.moves.popleft()
        if self.cooldown(now) > 0:
            return self.reject("ignored: cooldown")
        if len(self.moves) + count > self.settings.max_moves_per_min:
            return self.reject("ignored: moves per minute limit")
        return True

    def commit(self, target, presets, now):
        self.current = target
        self.candidate = None
        self.last_move = now
        self.last_target = self.smoothed if target != "wide" else None
        self.moves.extend([now] * len(presets))
        self.reason = "return to wide" if target == "wide" else f"target: {target}"
        return {"zone": target, "presets": dict(presets), "time": now}

    def tick(self, now):
        while self.moves and now - self.moves[0] >= 60:
            self.moves.popleft()
        if self.last_sample is not None and now - self.last_sample > 1:
            self.candidate = None
        s = self.settings
        if (self.last_voice is not None and now - self.last_voice >= s.silence_ms / 1000
                and self.current != "wide" and s.wide_presets):
            if self.ready(now, len(s.wide_presets)):
                return self.commit("wide", s.wide_presets, now)
        return None

    def feed(self, event, now):
        previous_sample = self.last_sample
        decision = self._feed(event, now)
        s = self.settings
        if not s.conversation_layout or not s.wide_presets or self.reason == "ignored: duplicate or old sample":
            return decision
        if (decision or self.last_sample != now or self.current == "wide"
                or self.reason in ("ignored: same zone", "accepted: same seat")):
            self.uncertain_since = None
        elif self.uncertain_since is None or previous_sample is None or now - previous_sample > 1:
            self.uncertain_since = now
        elif now - self.uncertain_since >= s.conversation_hold_ms / 1000 and self.ready(now, len(s.wide_presets)):
            self.uncertain_since = None
            return self.commit("wide", s.wide_presets, now)
        return decision

    def _feed(self, event, now):
        if event.get("mic_id", "primary") not in self.microphone_ids:
            return self.reject("microphone has no calibrated seats")
        s = self.settings
        if self.last_time is not None and now < self.last_time:
            return self.reject("ignored: out-of-order sample")
        self.last_time = now
        try:
            a, e = event["azimuth"], event["elevation"]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, e)):
                raise ValueError()
            if not 0 <= a <= 360 or not s.elevation_min <= e <= s.elevation_max:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            return self.reject("ignored: invalid angles", True)
        stamp = event.get("sample_time", event.get("time", now))
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            return self.reject("ignored: invalid timestamp", True)
        if self.last_event_time is not None and stamp <= self.last_event_time:
            return self.reject("ignored: duplicate or old sample")
        self.last_event_time = stamp
        activity, level = event.get("activity"), event.get("level")
        valid_level = isinstance(level, (int, float)) and not isinstance(level, bool) and math.isfinite(level)
        if event.get("beam_freeze") is True:
            return self.reject("ignored: far-end beam freeze", True)
        if (s.activity_gate and type(activity) is not bool) or (s.level_gate and not valid_level):
            return self.reject("ignored: requested speech signal unavailable", True)
        if activity is False or (s.level_gate and level < s.level_threshold):
            return self.reject("ignored: silence", True)
        # Unknown activity is explicitly position-only mode, not a claim of speech detection.
        self.last_voice = now
        if self.last_sample is not None and now - self.last_sample > 1:
            self.candidate = None
            self.window.clear()
            self.smoothed = None
        self.last_sample = now
        a %= 360
        self.window.append((a, e))
        ma = (a + median(delta(p[0], a) for p in self.window)) % 360
        me = median(p[1] for p in self.window)
        if self.smoothed is None:
            self.smoothed = (ma, me)
        else:
            olda, olde = self.smoothed
            self.smoothed = ((olda + s.ema_alpha * delta(ma, olda)) % 360,
                             olde + s.ema_alpha * (me - olde))
        if not self.history or now - self.history[-1]["time"] >= .05:
            self.history.append({"time": now, "raw": [a, e], "smooth": self.smoothed})
        while self.history and now - self.history[0]["time"] > 60:
            self.history.popleft()
        zones = [z for z in s.zones if zone_contains(z, self.smoothed, s.zone_margin_deg)]
        # Hysteresis: retain current zone through overlaps unless another zone is significantly closer.
        curr_z = next((z for z in zones if z.id == self.current), None)
        best_z = min(zones, key=lambda z: distance(self.smoothed, (z.azimuth, z.elevation)), default=None)
        if curr_z and best_z and best_z.id != curr_z.id:
            curr_dist = distance(self.smoothed, (curr_z.azimuth, curr_z.elevation))
            best_dist = distance(self.smoothed, (best_z.azimuth, best_z.elevation))
            zone = best_z if best_dist < curr_dist - s.deadband_deg else curr_z
        else:
            zone = curr_z or best_z
        if zone is None:
            return self.reject("ignored: outside zone margin", True)
        if zone.id == self.current:
            return self.reject("ignored: same zone", True)
        if self.last_target and distance(self.smoothed, self.last_target) < s.deadband_deg:
            return self.reject("ignored: within deadband", True)
        if zone.id != self.candidate:
            self.candidate, self.candidate_since = zone.id, now
        if now - self.candidate_since + 1e-9 < s.confirm_ms / 1000:
            return self.reject("confirming candidate")
        if not zone.presets:
            return self.reject("ignored: no presets assigned")
        if self.ready(now, len(zone.presets)):
            return self.commit(zone.id, zone.presets, now)
        return None

    def snapshot(self, now):
        return {"current": self.current, "candidate": self.candidate,
                "progress": min(1, max(0, (now - self.candidate_since) * 1000 / self.settings.confirm_ms)) if self.candidate else 0,
                "cooldown": round(self.cooldown(now), 1), "moves_per_min": len(self.moves),
                "smoothed": self.smoothed, "reason": self.reason,
                "history": [h for h in self.history if now - h["time"] <= 60]}
