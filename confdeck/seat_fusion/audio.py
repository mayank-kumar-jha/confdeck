"""Explicit per-microphone audio bindings; audio and VAD state never mix."""
import math
from pathlib import Path

from confdeck.aes67 import Aes67Stream
from confdeck.seat_fusion.aes67_channel import Aes67ChannelCapture
from confdeck.speech import GateConfig
from confdeck.speech_audio import AudioCapture


class IndependentAudio:
    """Own one capture and one recurrent speech model per configured source.

    Bindings must be configured by an operator: microphone identifiers cannot
    be inferred from a stream name or from the previous shared VAD. PCM stays
    in the capture worker. No input starts until start() is called.
    """

    def __init__(self, model_path, bindings, capture_factory=None):
        if not model_path or not Path(model_path).is_file():
            raise ValueError("A local speech model is required")
        if not isinstance(bindings, dict) or not 2 <= len(bindings) <= 8:
            raise ValueError("Configure between two and eight microphone audio bindings")
        self.captures = {}
        endpoints = set()
        for source, binding in bindings.items():
            if not isinstance(source, str) or not source or not isinstance(binding, dict):
                raise ValueError("Each audio binding needs a microphone identifier")
            kind = binding.get("kind")
            offset = binding.get("offset")
            if type(offset) not in (int, float) or not math.isfinite(offset):
                raise ValueError("Each microphone needs a measured audio offset")
            config = GateConfig(low_voice=False, offset=offset)
            if kind == "aes67":
                path = binding.get("sdp")
                if not isinstance(path, str) or not Path(path).is_file():
                    raise ValueError("Each AES67 binding needs an existing SDP file")
                stream = Aes67Stream.from_sdp(Path(path).read_text(encoding="utf-8"))
                channel = binding.get("channel", 1)
                if type(channel) is not int or not 1 <= channel <= stream.channels:
                    raise ValueError("Choose an existing AES67 channel for each microphone")
                endpoint = ("aes67", stream.group, stream.port, channel)
                kwargs = dict(model_path=model_path, sdp_path=path,
                              interface=binding.get("interface", "0.0.0.0"),
                              latency_ms=binding.get("latency_ms", 40), channel=channel, config=config)
                if type(kwargs["latency_ms"]) is not int or not 0 <= kwargs["latency_ms"] <= 250:
                    raise ValueError("AES67 latency must be from 0 to 250 ms")
                cls = Aes67ChannelCapture
            elif kind == "device":
                device, channel = binding.get("device"), binding.get("channel")
                if type(device) is not int or device < 0 or type(channel) is not int or not 1 <= channel <= 64:
                    raise ValueError("Each device binding needs an explicit device and channel")
                endpoint = ("device", device, channel)
                kwargs = dict(model_path=model_path, device=device, channel=channel, config=config)
                cls = AudioCapture
            else:
                raise ValueError("Audio binding kind must be aes67 or device")
            if endpoint in endpoints:
                raise ValueError("Microphones cannot share an audio endpoint")
            endpoints.add(endpoint)
            self.captures[source] = (capture_factory or (lambda capture_cls, **kw: capture_cls(**kw)))(cls, **kwargs)

    def start(self):
        started = []
        try:
            for capture in self.captures.values():
                capture.start()
                started.append(capture)
        except Exception:
            for capture in started:
                capture.stop()
            raise

    def stop(self):
        for capture in self.captures.values():
            capture.stop()

    def evidence(self, source, timestamp, now, max_age=.45):
        """Find an unexpired frame at or before a position sample, never another mic's."""
        capture = self.captures.get(source)
        if (capture is None or not all(type(v) in (int, float) and math.isfinite(v)
                                       for v in (timestamp, now, max_age)) or max_age <= 0):
            return None
        with capture.lock:
            frame = next((dict(f) for f in reversed(capture.frames) if f.get("time", math.inf) <= timestamp), None)
        if not frame or frame.get("clipping") or frame.get("no_signal"):
            return None
        frame_time = frame.get("time")
        offset = capture.config.offset if capture.config else 0.
        if (type(frame_time) not in (int, float) or not math.isfinite(frame_time)
                or not 0 <= timestamp - frame_time <= max_age
                or not 0 <= now - (frame_time - offset) <= max_age):
            return None
        reliability = frame.get("reliability")
        if type(reliability) not in (int, float) or not math.isfinite(reliability) or not 0 <= reliability <= 1:
            return None
        return frame

    def status(self):
        return {source: capture.snapshot()["status"] for source, capture in self.captures.items()}
