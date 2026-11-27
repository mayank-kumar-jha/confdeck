import struct
import threading
from collections import deque

import numpy as np
import pytest

from confdeck.aes67 import Aes67Error, Aes67Stream, _Decimator
from confdeck.seat_fusion.aes67_channel import ChannelReceiver, decode_channel
from confdeck.seat_fusion.audio import IndependentAudio
from confdeck.seat_fusion.controller import FusionController
from confdeck.seat_fusion.engine import SeatFusion
from confdeck.seat_fusion.model import LearnedSeatModel, room_signature, train
from confdeck.seat_fusion.replay import recorded_baseline, replay, trial_report
from confdeck.seat_fusion.shadow import ShadowService
from confdeck.tracking import TrackingSettings, Zone


def settings():
    return TrackingSettings(engine="new", zones=[
        Zone(id="a", name="A", mic_id="primary", azimuth=45, elevation=45, presets={"c": "1"}),
        Zone(id="b", name="B", mic_id="rear", azimuth=180, elevation=45, presets={"c": "2"}),
    ])


def event(source, t, **updates):
    return {"mic_id": source, "sample_time": t, "azimuth": 45 if source == "primary" else 180,
            "elevation": 45, **updates}


def feed_pair(engine, t, a=.8, b=0):
    first = engine.feed(event("primary", t), {"reliability": a}, t)
    second = engine.feed(event("rear", t), {"reliability": b}, t)
    return first or second


def test_shadow_selects_stable_seat_and_holds_ambiguous_interruption():
    engine = SeatFusion(settings())
    decisions = [feed_pair(engine, i/10) for i in range(20)]
    assert [d["seat"] for d in decisions if d] == ["a"]
    assert engine.current == "a"
    for i in range(20, 40):
        assert feed_pair(engine, i/10, .8, .8) is None
    assert engine.current == "a"
    assert "ambiguous" in engine.reason


def test_missing_audio_frozen_beam_and_stale_data_never_nominate():
    engine = SeatFusion(settings())
    for i in range(20):
        t = i/10
        assert engine.feed(event("primary", t), {"reliability": 1}, t) is None
        assert engine.feed(event("rear", t), None, t) is None
    assert engine.current is None
    assert "missing" in engine.reason
    for i in range(20, 40):
        t = i/10
        assert engine.feed(event("primary", t), {"reliability": 1}, t) is None
        assert engine.feed(event("rear", t, beam_freeze=True), {"reliability": 1}, t) is None
    assert engine.current is None


def test_duplicate_and_future_samples_cannot_extend_votes_or_poison_source():
    engine = SeatFusion(settings())
    feed_pair(engine, 1)
    engine.feed(event("primary", 100), {"reliability": 1}, 1.1)
    engine.feed(event("primary", 1.2), {"reliability": 1}, 1.2)
    assert engine.last_stamps["primary"] == 1.2
    engine.feed(event("rear", 1), {"reliability": 1}, 1.2)
    assert engine.observations["rear"][0] == 1
    assert engine.tick(2) is None
    assert "missing" in engine.reason


class FakeCapture:
    def __init__(self, config):
        self.config = config
        self.frames = deque()
        self.lock = threading.Lock()

    def snapshot(self):
        return {"status": "ready"}


def test_audio_binding_is_source_specific_and_rejects_duplicate_channels(tmp_path):
    model = tmp_path / "model.onnx"
    model.touch()
    duplicate = {"primary": {"kind": "device", "device": 1, "channel": 1, "offset": 0},
                 "rear": {"kind": "device", "device": 1, "channel": 1, "offset": 0}}
    def factory(cls, **kwargs):
        return FakeCapture(kwargs["config"])
    with pytest.raises(ValueError, match="share"):
        IndependentAudio(str(model), duplicate, factory)
    duplicate["rear"]["channel"] = 2
    audio = IndependentAudio(str(model), duplicate, factory)
    audio.captures["primary"].frames.append({"time": 1., "reliability": .8})
    audio.captures["rear"].frames.append({"time": 1., "reliability": 0.})
    assert audio.evidence("primary", 1.1, 1.1)["reliability"] == .8
    assert audio.evidence("rear", 1.1, 1.1)["reliability"] == 0.
    assert audio.evidence("rear", 1.1, 2.) is None
    assert audio.evidence("missing", 1.1, 1.1) is None


def test_stereo_aes67_bindings_require_distinct_channels(tmp_path):
    model = tmp_path / "model.onnx"
    model.touch()
    sdp = tmp_path / "stereo.sdp"
    sdp.write_text("v=0\ns=test\nc=IN IP4 239.69.0.10/32\nt=0 0\nm=audio 5004 RTP/AVP 96\na=rtpmap:96 L24/48000/2\n")
    binding = {"kind": "aes67", "sdp": str(sdp), "offset": 0}
    def factory(cls, **kwargs):
        return FakeCapture(kwargs["config"])
    with pytest.raises(ValueError, match="share"):
        IndependentAudio(str(model), {"primary": binding, "rear": binding}, factory)
    bank = IndependentAudio(str(model), {"primary": binding, "rear": {**binding, "channel": 2}}, factory)
    assert set(bank.captures) == {"primary", "rear"}


def test_stereo_aes67_decodes_independent_channels_without_downmixing():
    stereo_l24 = b"\x40\x00\x00\xc0\x00\x00"
    assert decode_channel(stereo_l24, "L24", 2, 1)[0] == pytest.approx(.5)
    assert decode_channel(stereo_l24, "L24", 2, 2)[0] == pytest.approx(-.5)
    assert decode_channel(b"\x40\x00\xc0\x00", "L16", 2, 2)[0] == pytest.approx(-.5)
    with pytest.raises(Aes67Error):
        decode_channel(stereo_l24, "L24", 2, 3)

    payload = b"\x40\x00\xc0\x00" * 1600
    packet = bytes((0x80, 96)) + struct.pack("!HI", 1, 10) + b"\x00\x00\x00\x01" + payload
    class FakeSocket:
        def recvfrom(self, size):
            return packet, None
    def receiver(channel):
        r = ChannelReceiver.__new__(ChannelReceiver)
        r.stream = Aes67Stream("239.69.0.10", 5004, 96, "L16", 48000, 2)
        r.channel, r.frame_samples, r.latency = channel, 512, 0
        r.socket, r.decimator = FakeSocket(), _Decimator()
        r.pending, r.pending_start = np.empty(0, dtype=np.float32), None
        r.output_index, r.base_time, r.base_rtp_timestamp = 0, None, None
        r.expected_sequence, r.expected_rtp_timestamp, r.last_packet = None, None, None
        r.last_sample_count, r.discontinuity = 0, False
        return r
    left = receiver(1).read_frame()[1]
    right = receiver(2).read_frame()[1]
    assert np.mean(left[50:]) == pytest.approx(.5, abs=.01)
    assert np.mean(right[50:]) == pytest.approx(-.5, abs=.01)


def test_replay_requires_source_tag_and_counts_missed_trial():
    session = {"tag": "test", "labels": [{"start": 0, "end": 3, "seat": "a", "condition": "normal"}], "rows": []}
    for i in range(30):
        t = i/10
        session["rows"].extend([
            {"kind": "speech_frame", "t": t, "mic_id": "primary", "reliability": .9},
            {"kind": "speech_frame", "t": t, "mic_id": "rear", "reliability": 0.},
            {"kind": "mic", "t": t, "event": event("primary", t)},
            {"kind": "mic", "t": t, "event": event("rear", t)},
        ])
    result = replay(session, settings())
    assert trial_report(session, result)["trial_accuracy"] == 1
    session["rows"] = [r for r in session["rows"] if r.get("mic_id") != "rear"]
    result = replay(session, settings())
    assert trial_report(session, result)["trial_accuracy"] == 0


def test_shadow_service_never_exposes_a_camera_command_and_compares_recorded_baseline():
    class FakeAudio:
        def __init__(self, model, bindings):
            self.captures = dict.fromkeys(bindings)

        def evidence(self, source, timestamp, now):
            return {"reliability": .9 if source == "primary" else 0.}

        def status(self):
            return {source: "ready" for source in self.captures}

    service = ShadowService(settings(), {"model": "unused", "audio": {"primary": {}, "rear": {}}}, FakeAudio)
    decisions = []
    for i in range(20):
        t = i / 10
        for source in ("primary", "rear"):
            decision, _ = service.feed(event(source, t), t)
            if decision:
                decisions.append(decision)
    assert len(decisions) == 1 and decisions[0]["shadow"] is True
    assert "presets" not in decisions[0]
    assert service.snapshot(2)["current"] == "a"
    session = {"tag": "test", "labels": [{"start": 0, "end": 3, "seat": "a", "condition": "normal"}],
               "rows": [{"kind": "decision", "t": 1, "seat": "b", "output": {"zone": "b"}}]}
    assert trial_report(session, recorded_baseline(session))["trial_accuracy"] == 0


def calibration_session(identity):
    rows, labels = [], []
    for class_index, seat in enumerate(("a", "b", None)):
        start = class_index*6
        labels.append({"start": start, "end": start+5.9, "seat": seat,
                       "condition": "normal" if seat else "noise"})
        for i in range(29):
            t = start + i*.2
            for source in ("primary", "rear"):
                active = (source == "primary" and seat == "a") or (source == "rear" and seat == "b")
                rows.append({"kind": "speech_frame", "t": t, "mic_id": source,
                             "reliability": .9 if active else .05})
                rows.append({"kind": "mic", "t": t, "event": event(source, t, level=-35 if active else -70)})
    return {"id": identity, "tag": "calibration", "rows": rows, "labels": labels}


def test_learned_fusion_requires_separate_calibration_and_room_signature():
    config = settings()
    first = calibration_session("one")
    second = calibration_session("two")
    trained = train([first, second], config)
    model = LearnedSeatModel(trained, expected_sources={"primary", "rear"},
                             expected_seats={"a", "b"}, expected_signature=room_signature(config))
    engine = SeatFusion(config, model=model)
    for i in range(20):
        t = i/10
        engine.feed(event("primary", t, level=-35), {"reliability": .9}, t)
        engine.feed(event("rear", t, level=-70), {"reliability": .05}, t)
    assert engine.current == "a"
    config.zones[0].azimuth = 46
    with pytest.raises(ValueError, match="calibration"):
        LearnedSeatModel(trained, expected_signature=room_signature(config))
    with pytest.raises(ValueError, match="distinct"):
        train([first, first], settings())
    second["tag"] = "test"
    with pytest.raises(ValueError, match="calibration"):
        train([first, second], settings())


def test_learned_model_cannot_recommend_a_seat_when_both_vads_are_silent():
    class OverconfidentModel:
        def predict(self, samples):
            return {"a": .99, "b": .005, "__none__": .005}

    engine = SeatFusion(settings(), model=OverconfidentModel())
    for i in range(20):
        t = i/10
        engine.feed(event("primary", t, level=-35), {"reliability": 0}, t)
        assert engine.feed(event("rear", t, level=-35), {"reliability": 0}, t) is None
    assert engine.current is None


def test_controller_enforces_room_hold_and_never_moves_on_ambiguity_or_outage():
    cfg = settings()
    cfg.min_hold_ms = 1000
    cfg.settle_ms = 0
    controller = FusionController(cfg)

    class Evidence:
        seat = "a"
        reason = "shadow recommendation"
        fresh = True
        def snapshot(self, now):
            return {"scorer": "trained", "current": self.seat, "reason": self.reason,
                    "sources": {"primary": self.fresh, "rear": self.fresh}}

    source = Evidence()
    assert controller.observe(source, 1)["zone"] == "a"
    source.seat = "b"
    assert controller.observe(source, 1.1) is None
    assert controller.current == "a" and controller.pending == "b"
    source.reason = "ambiguous seats / keep previous view"
    assert controller.observe(source, 2.1) is None
    assert controller.current == "a" and controller.pending is None
    source.reason = "same seat"
    source.fresh = False
    assert controller.observe(source, 2.2) is None
    assert controller.current == "a"
    source.fresh = True
    assert controller.observe(source, 2.3)["zone"] == "b"


def test_active_fusion_requires_a_trained_model():
    with pytest.raises(ValueError, match="trained room model"):
        ShadowService(settings(), {"model": "unused", "audio": {"primary": {}, "rear": {}}, "output": "active"})
