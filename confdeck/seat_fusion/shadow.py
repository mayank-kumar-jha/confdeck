"""Opt-in shadow coordinator. It has no camera-control interface."""
import json
import time
from pathlib import Path

from confdeck.seat_fusion.audio import IndependentAudio
from confdeck.seat_fusion.engine import FusionConfig, SeatFusion


class ShadowService:
    def __init__(self, settings, config, audio_factory=IndependentAudio):
        if not isinstance(config, dict) or set(config) - {"model", "audio", "seat_links", "fusion", "fusion_model", "output"}:
            raise ValueError("Invalid shadow configuration")
        if set(config.get("audio", {})) != {z.mic_id for z in settings.zones}:
            raise ValueError("Configure independent audio for every mapped microphone")
        self.links = config.get("seat_links", {})
        self.config = FusionConfig(**config.get("fusion", {}))
        self.model_path = config.get("fusion_model")
        self.output_mode = config.get("output", "shadow")
        if self.output_mode not in ("shadow", "active"):
            raise ValueError("Fusion output must be shadow or active")
        if self.output_mode == "active" and not self.model_path:
            raise ValueError("Active fusion requires a trained room model")
        self.engine = self.make_engine(settings)
        from confdeck.seat_fusion.controller import FusionController
        self.controller = FusionController(settings)
        self.audio = audio_factory(config.get("model"), config["audio"])
        self.status = "configured, not started"
        self.last_error = None

    @classmethod
    def from_file(cls, settings, path):
        with Path(path).open(encoding="utf-8") as source:
            return cls(settings, json.load(source))

    def start(self):
        self.audio.start()
        self.status = "running"

    def stop(self):
        self.audio.stop()
        self.status = "stopped"

    def configure(self, settings):
        if {z.mic_id for z in settings.zones} != set(self.audio.captures):
            raise ValueError("Microphone map changed; review shadow audio bindings")
        self.engine = self.make_engine(settings)
        from confdeck.seat_fusion.controller import FusionController
        self.controller = FusionController(settings)

    def make_engine(self, settings):
        model = None
        if self.model_path:
            from confdeck.seat_fusion.model import LearnedSeatModel, room_signature
            model = LearnedSeatModel.load(self.model_path,
                expected_sources={z.mic_id for z in settings.zones},
                expected_seats={self.links.get(z.id, z.id) for z in settings.zones},
                expected_signature=room_signature(settings, self.links))
        return SeatFusion(settings, self.links, self.config, model)

    def feed(self, event, now=None):
        now = time.monotonic() if now is None else now
        source = event.get("mic_id", "primary")
        stamp = event.get("sample_time")
        frame = self.audio.evidence(source, stamp, now)
        decision = self.engine.feed({**event, "mic_id": source}, frame, now)
        self.controller.observe(self.engine, now)
        return decision, frame

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        return {**self.engine.snapshot(now), "output": self.output_mode,
                "controlled_seat": self.controller.current, "control_reason": self.controller.reason,
                "status": self.status,
                "audio": self.audio.status(), "error": self.last_error}
