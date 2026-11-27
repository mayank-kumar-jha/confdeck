"""Small, opt-in AES67 RTP receiver for in-memory speech diagnostics.

Only single IPv4 multicast audio flows are accepted. PCM is never written to
disk; callers receive 512-sample, 16 kHz mono frames with monotonic timestamps.
"""

import ipaddress
import re
import socket
import struct
import sys
import time
from dataclasses import dataclass

import numpy as np


class Aes67Error(ValueError):
    """Invalid SDP or RTP audio packet."""


@dataclass(frozen=True)
class Aes67Stream:
    group: str
    port: int
    payload_type: int
    encoding: str
    sample_rate: int
    channels: int
    name: str = ""

    @classmethod
    def from_sdp(cls, text):
        session_connection = None
        media_connection = None
        audio = None
        mappings = {}
        name = ""
        in_audio = False
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("s=") and not name:
                name = line[2:].strip()
            elif line.startswith("m="):
                if in_audio:
                    break
                in_audio = line[2:].startswith("audio ")
                if in_audio:
                    fields = line[2:].split()
                    if len(fields) < 4 or fields[2] not in ("RTP/AVP", "RTP/AVPF"):
                        raise Aes67Error("SDP must define one RTP audio stream")
                    try:
                        port = int(fields[1].split("/")[0])
                        payload_type = int(fields[3])
                    except ValueError as error:
                        raise Aes67Error("Invalid SDP audio port or payload type") from error
                    audio = (port, payload_type)
                    media_connection = None
            elif line.startswith("c="):
                fields = line[2:].split()
                if len(fields) != 3 or fields[0] != "IN" or fields[1] != "IP4":
                    raise Aes67Error("AES67 receiver requires an IPv4 connection address")
                address = fields[2].split("/")[0]
                if in_audio:
                    media_connection = address
                else:
                    session_connection = address
            elif in_audio and line.startswith("a=rtpmap:"):
                match = re.fullmatch(r"a=rtpmap:(\d+)\s+([A-Za-z0-9]+)/(\d+)(?:/(\d+))?", line)
                if not match:
                    raise Aes67Error("Invalid SDP RTP mapping")
                pt, encoding, rate, channels = match.groups()
                mappings[int(pt)] = (encoding.upper(), int(rate), int(channels or 1))

        if audio is None:
            raise Aes67Error("SDP contains no audio stream")
        port, payload_type = audio
        group = media_connection or session_connection
        mapping = mappings.get(payload_type)
        if not group or not mapping:
            raise Aes67Error("SDP is missing the audio multicast address or RTP mapping")
        try:
            address = ipaddress.ip_address(group)
        except ValueError as error:
            raise Aes67Error("Invalid SDP multicast address") from error
        encoding, sample_rate, channels = mapping
        if (address.version != 4 or not address.is_multicast or not 1 <= port <= 65535
                or not 0 <= payload_type <= 127):
            raise Aes67Error("SDP must use an IPv4 multicast group and valid UDP port")
        if encoding not in ("L24", "L16") or sample_rate != 48000 or channels not in (1, 2):
            raise Aes67Error("Only 48 kHz mono/stereo AES67 L24 or L16 is supported")
        return cls(str(address), port, payload_type, encoding, sample_rate, channels, name)


@dataclass(frozen=True)
class RtpPacket:
    sequence: int
    timestamp: int
    payload: bytes


def parse_rtp_packet(data, payload_type):
    """Return an RTP packet, or None for a different dynamic payload type."""
    if len(data) < 12:
        raise Aes67Error("RTP packet is shorter than its fixed header")
    first, second = data[0], data[1]
    if first >> 6 != 2:
        raise Aes67Error("Unsupported RTP version")
    if (second & 0x7F) != payload_type:
        return None
    offset = 12 + 4 * (first & 0x0F)
    if offset > len(data):
        raise Aes67Error("Truncated RTP CSRC list")
    if first & 0x10:
        if offset + 4 > len(data):
            raise Aes67Error("Truncated RTP extension header")
        words = struct.unpack_from("!H", data, offset + 2)[0]
        offset += 4 + words * 4
    end = len(data)
    if first & 0x20:
        padding = data[-1]
        if padding == 0 or padding > end - offset:
            raise Aes67Error("Invalid RTP padding")
        end -= padding
    if offset >= end:
        raise Aes67Error("RTP packet has no audio payload")
    sequence, timestamp = struct.unpack_from("!HI", data, 2)
    return RtpPacket(sequence, timestamp, data[offset:end])


def decode_pcm(payload, encoding, channels):
    """Decode big-endian AES67 PCM and downmix to normalized mono float32."""
    bytes_per_sample = 3 if encoding == "L24" else 2
    stride = bytes_per_sample * channels
    if not payload or len(payload) % stride:
        raise Aes67Error("RTP payload is not a whole number of audio samples")
    if encoding == "L24":
        values = np.frombuffer(payload, dtype=np.uint8).reshape(-1, channels, 3).astype(np.int32)
        values = (values[:, :, 0] << 16) | (values[:, :, 1] << 8) | values[:, :, 2]
        values = (values ^ 0x800000) - 0x800000
        values = values.astype(np.float32) / 8388608.0
    elif encoding == "L16":
        values = np.frombuffer(payload, dtype=">i2").reshape(-1, channels)
        values = values.astype(np.float32) / 32768.0
    else:
        raise Aes67Error("Unsupported AES67 PCM encoding")
    return values.mean(axis=1, dtype=np.float32) if channels > 1 else values[:, 0]


class _Decimator:
    """Band-limited integer 48 kHz to 16 kHz decimator with packet continuity."""

    def __init__(self):
        taps_count = 63
        n = np.arange(taps_count, dtype=np.float64) - (taps_count - 1) / 2
        taps = np.sinc(n / 3.0) / 3.0
        taps *= np.hamming(taps_count)
        self.taps = (taps / taps.sum()).astype(np.float32)
        self.delay = (taps_count - 1) // 2
        self.history = np.zeros(taps_count - 1, dtype=np.float32)
        self.seen = 0

    def reset(self):
        self.history.fill(0)
        self.seen = 0

    def process(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            return samples, np.empty(0, dtype=np.int64)
        joined = np.concatenate((self.history, samples))
        filtered = np.convolve(joined, self.taps, mode="valid").astype(np.float32)
        endpoints = self.seen + np.arange(filtered.size, dtype=np.int64)
        centers = endpoints - self.delay
        select = (centers >= 0) & (centers % 3 == 0)
        self.history = joined[-(self.taps.size - 1):].copy()
        self.seen += samples.size
        return filtered[select], centers[select] // 3


class Aes67Receiver:
    """Join one SDP multicast flow and yield timestamped 16 kHz PCM frames."""

    def __init__(self, stream, interface="0.0.0.0", latency_ms=40, frame_samples=512):
        if not 0 <= latency_ms <= 250:
            raise ValueError("AES67 jitter delay must be from 0 to 250 ms")
        self.stream = stream
        self.frame_samples = frame_samples
        self.latency = latency_ms / 1000.0
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Multiple AES67 microphones can use port 5004. On Linux the default
        # IP_MULTICAST_ALL delivers packets from unrelated joined groups too,
        # so constrain this socket to the group it explicitly joins.
        if sys.platform.startswith("linux"):
            self.socket.setsockopt(socket.IPPROTO_IP, getattr(socket, "IP_MULTICAST_ALL", 49), 0)
        self.socket.bind(("", stream.port))
        self.socket.settimeout(0.1)
        membership = socket.inet_aton(stream.group) + socket.inet_aton(interface)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        self.decimator = _Decimator()
        self.pending = np.empty(0, dtype=np.float32)
        self.pending_start = None
        self.output_index = 0
        self.base_time = None
        self.base_rtp_timestamp = None
        self.expected_sequence = None
        self.expected_rtp_timestamp = None
        self.last_packet = None
        self.last_sample_count = 0
        self.discontinuity = False

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _append(self, samples, center_indices):
        if not samples.size:
            return
        if self.pending.size == 0:
            self.pending_start = int(center_indices[0])
        self.pending = np.concatenate((self.pending, samples))

    def read_frame(self, stop_event=None):
        """Block for one frame; raise TimeoutError after a sustained packet outage."""
        while self.pending.size < self.frame_samples:
            if stop_event is not None and stop_event.is_set():
                return None
            try:
                data, _ = self.socket.recvfrom(65535)
            except socket.timeout:
                if self.last_packet is not None and time.monotonic() - self.last_packet > 1.0:
                    raise TimeoutError("No AES67 RTP packets for 1 second")
                continue
            now = time.monotonic()
            packet = parse_rtp_packet(data, self.stream.payload_type)
            if packet is None:
                continue
            pcm = decode_pcm(packet.payload, self.stream.encoding, self.stream.channels)
            if self.expected_sequence is not None:
                sequence_gap = (packet.sequence - self.expected_sequence) & 0xFFFF
                if sequence_gap >= 0x8000:
                    continue  # Duplicate or late packet.
            else:
                sequence_gap = 0
            if self.base_rtp_timestamp is None:
                self.base_rtp_timestamp = packet.timestamp
                self.base_time = now - pcm.size / self.stream.sample_rate - self.latency
            elif sequence_gap:
                gap_samples = (packet.timestamp - self.expected_rtp_timestamp) & 0xFFFFFFFF
                if gap_samples >= 0x80000000:
                    continue
                if gap_samples <= self.stream.sample_rate // 10:
                    zeros = np.zeros(gap_samples, dtype=np.float32)
                    output, indices = self.decimator.process(zeros)
                    self._append(output, indices)
                else:
                    self.decimator.reset()
                    self.pending = np.empty(0, dtype=np.float32)
                    self.output_index = 0
                    self.base_rtp_timestamp = packet.timestamp
                    self.base_time = now - pcm.size / self.stream.sample_rate - self.latency
                self.discontinuity = True
            output, indices = self.decimator.process(pcm)
            self._append(output, indices)
            self.expected_sequence = (packet.sequence + 1) & 0xFFFF
            self.expected_rtp_timestamp = (packet.timestamp + pcm.size) & 0xFFFFFFFF
            self.last_sample_count = pcm.size
            self.last_packet = now

        frame = self.pending[:self.frame_samples].copy()
        self.pending = self.pending[self.frame_samples:]
        start = self.pending_start
        self.pending_start += self.frame_samples
        timestamp_delta = (start * 3) / self.stream.sample_rate
        timestamp = self.base_time + timestamp_delta
        discontinuity, self.discontinuity = self.discontinuity, False
        return timestamp, frame, discontinuity
