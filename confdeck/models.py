"""Validated, credential-free persistent configuration."""

import ipaddress
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from confdeck.tracking import TrackingSettings

PRIVATE = tuple(
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
)


def local_ip(value: str) -> str:
    """Accept only literal RFC1918 or loopback IPv4 addresses, without DNS."""
    ip = ipaddress.ip_address(value)
    if ip.version != 4 or not any(ip in net for net in PRIVATE):
        raise ValueError("Use a local IPv4 address, such as 192.168.1.20.")
    return str(ip)


def private_subnet(value: str) -> ipaddress.IPv4Network:
    """Constrain discovery to one private /24; never scan the public Internet."""
    net = ipaddress.ip_network(value, strict=False)
    if net.version != 4 or net.prefixlen != 24 or not any(net.subnet_of(n) for n in PRIVATE[:3]):
        raise ValueError("Choose a private /24 network, for example 192.168.1.0/24.")
    return net


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Camera(Model):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12], pattern=r"^[a-z0-9_-]{1,40}$")
    name: str = Field(default="Meeting room camera", min_length=1, max_length=80)
    ip: str
    port: int = Field(default=554, ge=1, le=65535)
    path: str = "/rtpstream/config1"
    model: str = Field(default="RTSP camera", max_length=120)
    demo: bool = False
    transcode_h264: bool = False
    copy_h264: bool = False
    control_protocol: Literal["none", "canon", "onvif", "visca"] = "none"
    control_enabled: bool = False
    handoff_silence_s: int = Field(default=10, ge=0, le=120)
    handoff_reanchor_s: int = Field(default=0, ge=0, le=3600)
    prep_camera: bool = False
    handoff_mode: Literal["off", "dry", "live"] = "off"
    protected_presets: list[int] = Field(default_factory=lambda: [80, 81], max_length=128)
    native_tracking: bool = False
    visca_port: int = Field(default=52381, ge=1, le=65535)
    control_port: int = Field(default=80, ge=1, le=65535)
    control_profile: str = Field(default="", max_length=128)
    preset_start: int = Field(default=1, ge=0, le=127)
    preset_end: int = Field(default=100, ge=0, le=127)
    preset_records: dict[str, str] = Field(default_factory=dict)
    preset_positions: dict[str, tuple[int, int, int]] = Field(default_factory=dict)
    preset_speeds: dict[str, float] = Field(default_factory=dict)
    preset_speed_verified: bool = False
    visca_local_port: int = Field(default=0, ge=0, le=65535)

    @model_validator(mode="after")
    def valid_preset_range(self):
        if self.preset_start > self.preset_end:
            raise ValueError("Preset range start must not exceed its end.")
        return self

    _ip = field_validator("ip")(local_ip)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not value.startswith("/") or len(value) > 500 or re.search(r"[\s@#\\]", value):
            raise ValueError("Enter an RTSP path beginning with /, without a host or credentials.")
        if re.search(r"(?i)(password|passwd|token|username|user|pwd)=", value):
            raise ValueError("Put camera credentials in the username and password fields.")
        return value


class MicSettings(Model):
    ip: str = "192.168.1.92"
    port: int = Field(default=443, ge=1, le=65535)
    protocol: Literal["sscv2", "tcc2"] = "sscv2"
    enabled: bool = False
    azimuth_offset: float = Field(default=0, ge=-360, le=360)
    invert: bool = False
    elevation_reference: Literal["vertical", "horizontal"] = "vertical"
    ceiling_height: float = Field(default=3, gt=0, le=30)
    head_height: float = Field(default=1.2, ge=0, le=5)
    room_x: float = Field(default=0.0, ge=-100.0, le=100.0)
    room_y: float = Field(default=0.0, ge=-100.0, le=100.0)
    room_yaw: float = Field(default=0.0, ge=-360.0, le=360.0)
    estimate_position: bool = False
    beam_width: float = Field(default=30, ge=5, le=90)
    tls_fingerprint: str = ""
    resource_paths: list[str] = Field(
        default_factory=lambda: ["/api/audio/inputs/microphone/beam/direction"]
    )
    field_map: dict[str, str] = Field(
        default_factory=lambda: {"azimuth": "azimuth", "elevation": "elevation"}
    )

    _ip = field_validator("ip")(local_ip)

    @field_validator("tls_fingerprint")
    @classmethod
    def fingerprint(cls, value: str) -> str:
        value = value.replace(":", "").lower().strip()
        if value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(
                "Use the device certificate's SHA-256 fingerprint (64 hex characters)."
            )
        return value

    @field_validator("resource_paths")
    @classmethod
    def valid_resource_paths(cls, value: list[str]) -> list[str]:
        if not 1 <= len(value) <= 20:
            raise ValueError("Enter between 1 and 20 resource paths.")
        for p in value:
            if not re.fullmatch(r"/[a-zA-Z0-9/_-]{1,200}", p):
                raise ValueError(f"Invalid resource path: {p!r}.")
        return value

    @field_validator("field_map")
    @classmethod
    def valid_field_map(cls, value: dict[str, str]) -> dict[str, str]:
        allowed_keys = {"azimuth", "elevation", "activity", "level"}
        for k, v in value.items():
            if k not in allowed_keys:
                raise ValueError(f"Unknown mapping key {k!r}.")
            if not re.fullmatch(r"[a-zA-Z0-9_]{1,80}", v):
                raise ValueError(f"Invalid field name {v!r}.")
        return value

    @model_validator(mode="after")
    def heights(self):
        if self.head_height >= self.ceiling_height:
            raise ValueError("Ceiling height must be above head height.")
        return self


class AdditionalMic(MicSettings):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12], pattern=r"^[a-z0-9_-]{1,40}$")


class DisplaySettings(Model):
    layout: Literal["single", "two", "three", "grid", "pip"] = "grid"
    cameras: list[str] = Field(default_factory=list, max_length=4)
    blank: bool = False


class Scene(Model):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    presets: dict[str, str] = Field(min_length=1, max_length=16)


class Settings(Model):
    scenes: list[Scene] = Field(default_factory=list, max_length=64)
    schema_version: int = 1
    room_name: str = Field(default="Meeting room", min_length=1, max_length=80)
    wizard_done: bool = False
    cameras: list[Camera] = Field(default_factory=list, max_length=16)
    mic: MicSettings = Field(default_factory=MicSettings)
    microphones: list[AdditionalMic] = Field(default_factory=list, max_length=7)
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    preview_limit: int = Field(default=4, ge=1, le=4)
    display_host: str = "127.0.0.1"
    calibration: list[dict] = Field(default_factory=list, max_length=100)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)

    _display_host = field_validator("display_host")(local_ip)

    @model_validator(mode="after")
    def unique_cameras(self):
        mic_ids = [m.id for m in self.microphones]
        if "primary" in mic_ids or len(mic_ids) != len(set(mic_ids)):
            raise ValueError("Microphone identifiers must be unique; primary is reserved.")
        endpoints = [(m.ip, m.port) for m in [self.mic, *self.microphones] if m.enabled]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError("This microphone is already connected.")
        ips = [(c.ip, c.port) for c in self.cameras if not c.demo]
        if len(ips) != len(set(ips)):
            raise ValueError("This camera is already added. Rename or edit the existing camera.")
        ids = [c.id for c in self.cameras]
        if len(ids) != len(set(ids)):
            raise ValueError("Camera identifiers must be unique.")
        return self
