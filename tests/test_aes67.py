import struct
from types import SimpleNamespace

import numpy as np
import pytest

from confdeck.aes67 import (
    Aes67Error,
    Aes67Receiver,
    Aes67Stream,
    _Decimator,
    decode_pcm,
    parse_rtp_packet,
)

SDP = """v=0
o=- 123 1 IN IP4 192.168.1.93
s=TCCM-2aaee6-LocalOut
c=IN IP4 239.69.0.10/32
t=0 0
m=audio 5004 RTP/AVP 96
a=rtpmap:96 L24/48000/1
a=ptime:1
"""


def test_parse_aes67_sdp():
    stream = Aes67Stream.from_sdp(SDP)
    assert stream.group == "239.69.0.10"
    assert stream.port == 5004
    assert stream.payload_type == 96
    assert stream.encoding == "L24"
    assert stream.sample_rate == 48000
    assert stream.channels == 1
    assert stream.name == "TCCM-2aaee6-LocalOut"


def test_linux_receiver_restricts_packets_to_its_joined_multicast_group(monkeypatch):
    from confdeck import aes67
    calls = []
    class FakeSocket:
        def setsockopt(self, *args): calls.append(args)
        def bind(self, address): pass
        def settimeout(self, timeout): pass
        def close(self): pass
    monkeypatch.setattr(aes67, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(aes67.socket, "socket", lambda *args: FakeSocket())
    receiver = Aes67Receiver(Aes67Stream.from_sdp(SDP))
    assert (aes67.socket.IPPROTO_IP, getattr(aes67.socket, "IP_MULTICAST_ALL", 49), 0) in calls
    receiver.close()


@pytest.mark.parametrize(
    "sdp",
    [
        SDP.replace("239.69.0.10", "192.168.1.93"),
        SDP.replace("48000", "16000"),
        SDP.replace("L24/48000/1", "opus/48000/1"),
        SDP.replace("5004", "99999"),
    ],
)
def test_reject_unsupported_or_unicast_sdp(sdp):
    with pytest.raises(Aes67Error):
        Aes67Stream.from_sdp(sdp)


def test_parse_rtp_extension_padding_and_dynamic_payload():
    payload = b"\x7f\xff\xff"
    packet = (
        bytes((0xB1, 96))  # RTPv2, padding, extension, one CSRC.
        + struct.pack("!HI", 19, 1234)
        + b"\0\0\0\1"
        + b"\xbe\xde\0\1"
        + b"\0\0\0\0"
        + payload
        + b"\x00\x02"
    )
    parsed = parse_rtp_packet(packet, 96)
    assert parsed.sequence == 19
    assert parsed.timestamp == 1234
    assert parsed.payload == payload
    assert parse_rtp_packet(packet, 97) is None


def test_decode_signed_big_endian_l24_and_stereo_downmix():
    mono = decode_pcm(b"\x7f\xff\xff\x80\x00\x00\x00\x00\x00", "L24", 1)
    assert mono[0] == pytest.approx(1.0, abs=1e-6)
    assert mono[1] == pytest.approx(-1.0)
    assert mono[2] == 0

    stereo = decode_pcm(b"\x40\x00\x00\xc0\x00\x00", "L24", 2)
    assert stereo[0] == pytest.approx(0.0)


def test_decode_l16_and_decimator_preserve_16khz_rate():
    samples = decode_pcm(b"\x40\x00\xc0\x00", "L16", 2)
    assert samples.tolist() == pytest.approx([0.0])

    decimator = _Decimator()
    output = []
    for _ in range(10):
        block = np.ones(4800, dtype=np.float32) * 0.25
        values, _ = decimator.process(block)
        output.extend(values)
    assert 15980 <= len(output) <= 16000
    assert np.mean(output[100:]) == pytest.approx(0.25, abs=0.01)
