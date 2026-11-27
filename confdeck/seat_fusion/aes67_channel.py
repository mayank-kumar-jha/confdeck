"""Select one channel from an AES67 flow without downmixing microphone audio."""
import math
import socket
import time

import numpy as np

from confdeck.aes67 import Aes67Error, Aes67Receiver, Aes67Stream, parse_rtp_packet
from confdeck.speech_audio import AudioCapture


def decode_channel(payload, encoding, channels, channel):
    """Decode an explicitly selected one-based channel from L16 or L24 RTP."""
    if type(channel) is not int or not 1 <= channel <= channels:
        raise Aes67Error("Invalid AES67 input channel")
    if encoding not in ("L16", "L24") or channels not in (1, 2):
        raise Aes67Error("Unsupported AES67 audio format")
    width = 3 if encoding == "L24" else 2
    if not payload or len(payload) % (width*channels):
        raise Aes67Error("RTP payload is not a whole number of audio samples")
    if encoding == "L16":
        values = np.frombuffer(payload, dtype=">i2").reshape(-1, channels)[:, channel-1]
        return values.astype(np.float32) / 32768.0
    values = np.frombuffer(payload, dtype=np.uint8).reshape(-1, channels, 3)[:, channel-1, :].astype(np.int32)
    values = (values[:, 0] << 16) | (values[:, 1] << 8) | values[:, 2]
    return ((values ^ 0x800000) - 0x800000).astype(np.float32) / 8388608.0


class ChannelReceiver(Aes67Receiver):
    def __init__(self, stream, interface="0.0.0.0", latency_ms=40, channel=1):
        if type(channel) is not int or not 1 <= channel <= stream.channels:
            raise ValueError("AES67 channel not present in the stream")
        self.channel = channel
        super().__init__(stream, interface, latency_ms)

    def read_frame(self, stop_event=None):
        while self.pending.size < self.frame_samples:
            if stop_event is not None and stop_event.is_set():
                return None
            try:
                data, _ = self.socket.recvfrom(65535)
            except socket.timeout:
                if self.last_packet is not None and time.monotonic()-self.last_packet > 1:
                    raise TimeoutError("No AES67 RTP packets for 1 second")
                continue
            now = time.monotonic()
            packet = parse_rtp_packet(data, self.stream.payload_type)
            if packet is None:
                continue
            pcm = decode_channel(packet.payload, self.stream.encoding, self.stream.channels, self.channel)
            if self.expected_sequence is None:
                gap = 0
            else:
                gap = (packet.sequence-self.expected_sequence) & 0xFFFF
                if gap >= 0x8000:
                    continue
            if self.base_rtp_timestamp is None:
                self.base_rtp_timestamp = packet.timestamp
                self.base_time = now-pcm.size/self.stream.sample_rate-self.latency
            elif gap:
                missing = (packet.timestamp-self.expected_rtp_timestamp) & 0xFFFFFFFF
                if missing >= 0x80000000:
                    continue
                if missing <= self.stream.sample_rate//10:
                    output, indices = self.decimator.process(np.zeros(missing, dtype=np.float32))
                    self._append(output, indices)
                else:
                    self.decimator.reset()
                    self.pending = np.empty(0, dtype=np.float32)
                    self.output_index = 0
                    self.base_rtp_timestamp = packet.timestamp
                    self.base_time = now-pcm.size/self.stream.sample_rate-self.latency
                self.discontinuity = True
            output, indices = self.decimator.process(pcm)
            self._append(output, indices)
            self.expected_sequence = (packet.sequence+1) & 0xFFFF
            self.expected_rtp_timestamp = (packet.timestamp+pcm.size) & 0xFFFFFFFF
            self.last_sample_count = pcm.size
            self.last_packet = now
        frame = self.pending[:self.frame_samples].copy()
        self.pending = self.pending[self.frame_samples:]
        start = self.pending_start
        self.pending_start += self.frame_samples
        timestamp = self.base_time + start*3/self.stream.sample_rate
        discontinuity, self.discontinuity = self.discontinuity, False
        return timestamp, frame, discontinuity


class Aes67ChannelCapture(AudioCapture):
    def __init__(self, model_path, sdp_path, interface="0.0.0.0", latency_ms=40, channel=1, config=None):
        super().__init__(model_path, device=None, channel=1, config=config)
        self.sdp_path, self.interface, self.latency_ms, self.audio_channel = sdp_path, interface, latency_ms, channel

    def _worker(self):
        from confdeck.speech import SileroFrames, SpeechGate
        try:
            with open(self.sdp_path, encoding="utf-8") as source:
                stream = Aes67Stream.from_sdp(source.read())
            if not 1 <= self.audio_channel <= stream.channels:
                raise ValueError("AES67 channel not present")
            model = SileroFrames(self.model_path)
        except Exception as error:
            self.status = f"AES67 input unavailable ({type(error).__name__}: {error})"
            return
        while not self.stop_event.is_set():
            gate, previous = SpeechGate(self.config), None
            model.reset()
            try:
                with ChannelReceiver(stream, self.interface, self.latency_ms, self.audio_channel) as receiver:
                    self.status = "Waiting for independent AES67 audio"
                    while not self.stop_event.is_set():
                        result = receiver.read_frame(self.stop_event)
                        if result is None:
                            break
                        stamp, samples, discontinuity = result
                        if not math.isfinite(stamp) or time.monotonic()-stamp > .35:
                            model.reset()
                            gate, previous = SpeechGate(self.config), None
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                            continue
                        if previous is not None and (stamp <= previous or stamp-previous > .5):
                            model.reset()
                            gate = SpeechGate(self.config)
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                        if discontinuity:
                            model.reset()
                            gate = SpeechGate(self.config)
                            with self.lock:
                                self.latest = None
                                self.frames.clear()
                        previous = stamp
                        info = model.process(samples)
                        frame = gate.push(stamp, info["level"], info["probability"])
                        frame.update(clipping=info["clipping"], no_signal=info["no_signal"])
                        self._record_scalar(frame)
                        with self.lock:
                            self.latest, self.latest_received = frame, time.monotonic()
                            self.frames.append(frame)
                            self.status = ("No signal" if info["no_signal"] else
                                           "Clipping; check source level" if info["clipping"] else gate.status)
            except Exception as error:
                with self.lock:
                    self.latest = None
                    self.frames.clear()
                    self.status = f"AES67 input unavailable ({type(error).__name__}); retrying"
                self.stop_event.wait(2)
