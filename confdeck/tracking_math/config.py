import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Convention(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    azimuth_zero: float = Field(default=0, ge=-360, le=360)
    clockwise: bool = False
    elevation_reference: Literal["horizontal", "vertical"] = "horizontal"
    orientation_offset: float = Field(default=0, ge=-180, le=180)


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mean: tuple[float, float, float]
    kappa: float = Field(ge=.01, le=10000)
    weight: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def unit(self):
        if abs(sum(x*x for x in self.mean)-1) > .001:
            raise ValueError("Component mean must be a unit vector")
        return self


class InferenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    convention: Convention = Field(default_factory=Convention)
    p_on: float = Field(default=.78, gt=.5, lt=1)
    switch_p_on: float = Field(default=.9, gt=.5, lt=1)
    evidence_floor_ms: int = Field(default=250, ge=100, le=2000)
    p_off: float = Field(default=.35, gt=0, lt=.5)
    margin: float = Field(default=.2, ge=0, le=1)
    switch_probability: float = Field(default=.04, gt=0, lt=.5)
    release_ms: int = Field(default=600, ge=100, le=10000)
    energy_ms: int = Field(default=200, ge=100, le=2000)
    noise_margin: float = Field(default=8, ge=0, le=30)
    parked_enabled: bool = True
    parked_azimuth: float = Field(default=315, ge=0, lt=360)
    parked_elevation: float = Field(default=90, ge=0, le=90)
    parked_tolerance: float = Field(default=1, ge=0, le=10)
    frozen_seconds: float = Field(default=4, ge=1, le=60)
    jump_degrees: float = Field(default=100, ge=30, le=180)
    opposite_multiplier: float = Field(default=1.25, ge=1, le=5)
    vision_weight: float = Field(default=.15, ge=0, le=1)
    vision_veto_only: bool = False

    @model_validator(mode="after")
    def barriers(self):
        if self.switch_p_on < self.p_on:
            raise ValueError("Switch barrier must be at least the first-lock barrier")
        return self


class ROI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str = Field(min_length=1, max_length=40)
    preset: str = Field(default="", max_length=128)
    seat: str = Field(min_length=1, max_length=40)
    rect: tuple[int, int, int, int]
    masks: list[tuple[int, int, int, int]] = Field(default_factory=list, max_length=8)
    kind: Literal["seat", "teaching"] = "seat"
    face_signature: list[float] | None = Field(default=None, max_length=256)
    signature_threshold: float = Field(default=0.45, ge=0.0, le=1.0)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value):
        text = str(value or "seat").strip().lower()
        if text in ("blackboard", "board", "screen", "presenter", "teacher", "teaching_zone"):
            return "teaching"
        return text or "seat"

    @field_validator("face_signature")
    @classmethod
    def valid_signature(cls, value):
        if value is None:
            return None
        if not value:
            return None
        cleaned = []
        for item in value:
            num = float(item)
            if not math.isfinite(num):
                raise ValueError("face_signature must contain finite floats")
            cleaned.append(num)
        return cleaned

    @model_validator(mode="after")
    def bounds(self):
        for x, y, w, h in [self.rect, *self.masks]:
            if x < 0 or y < 0 or w < 1 or h < 1 or x+w > 320 or y+h > 180:
                raise ValueError("ROI must fit within 320x180")
        return self


class VisionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["off", "advisory", "active"] = "off"
    camera_modes: dict[str, Literal["off", "advisory", "active"]] = Field(default_factory=dict, max_length=16)
    rois: list[ROI] = Field(default_factory=list, max_length=64)
    llr_models: dict[str, dict[str, float]] = Field(default_factory=dict, max_length=64)
    mouth_model: str = Field(default="", max_length=512)
    track_after_preset: bool = True
    lock_hold_s: float = Field(default=3.0, ge=0.2, le=30.0)
    teaching_zone_priority: bool = True

    @model_validator(mode="after")
    def models(self):
        for model in self.llr_models.values():
            if set(model) != {"speaking_mean", "silent_mean", "variance"} or model["variance"] <= 0:
                raise ValueError("LLR models require speaking_mean, silent_mean and positive variance")
        return self
