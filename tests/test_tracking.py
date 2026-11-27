import asyncio
import json

import pytest
from pydantic import ValidationError

from confdeck.microphone import PositionParser, tracking_scenario
from confdeck.models import Camera, MicSettings
from confdeck.runtime import Runtime
from confdeck.store import Store
from confdeck.tracking import TrackingFilter, TrackingSettings, Zone, delta, zone_contains
from confdeck.visca import ViscaIpDriver


def config(**kwargs):
    return TrackingSettings(zones=[
        Zone(id="one", name="Seat 1", azimuth=0, elevation=45, radius=30, presets={"cam": "1"}),
        Zone(id="two", name="Seat 2", azimuth=180, elevation=45, radius=30, presets={"cam": "2"}),
    ], wide_presets={"cam": "0"}, **kwargs)


def event(a=0, **kwargs):
    return {"azimuth": a, "elevation": 45, "activity": True, **kwargs}


def feed_for(engine, start, end, a=0):
    decisions = []
    for i in range(round(start * 10), round(end * 10) + 1):
        d = engine.feed(event(a), i / 10)
        if d:
            decisions.append(d)
    return decisions


def test_wrap_median_and_ema():
    f = TrackingFilter(config(median_n=3))
    for i, a in enumerate([359, 0, 1, 359, 2, 0]):
        f.feed(event(a), i / 10)
    assert abs(delta(f.smoothed[0], 0)) < 2


def test_stationary_speaker_confirms_and_does_not_repeat():
    f = TrackingFilter(config())
    moves = feed_for(f, 0, 10)
    assert len(moves) == 1 and moves[0]["time"] == 1


def test_flapping_and_short_noise_do_not_confirm():
    f = TrackingFilter(config(ema_alpha=1))
    for i in range(100):
        assert f.feed(event(0 if i % 2 else 180), i / 10) is None
    f = TrackingFilter(config())
    for row in tracking_scenario("noise", [(0, 45), (180, 45)]):
        assert f.feed(row["event"], row["t"]) is None


def test_deadband_and_zone_margin():
    s = TrackingSettings(deadband_deg=35, zone_margin_deg=5, ema_alpha=1, zones=[
        Zone(id='a', name='A', azimuth=0, elevation=45, radius=15, presets={'cam': '1'}),
        Zone(id='b', name='B', azimuth=30, elevation=45, radius=15, presets={'cam': '2'}),
    ])
    f = TrackingFilter(s)
    assert not feed_for(f, 0, 2, 13)  # in the zone but not inside its margin
    assert len(feed_for(f, 2.1, 4, 0)) == 1
    assert not feed_for(f, 4.1, 10, 30)
    assert f.reason == 'ignored: within deadband'


def test_coordinate_ranges_allow_wrap_and_switch_between_seats():
    settings = TrackingSettings(
        confirm_ms=100,
        min_hold_ms=500,
        zone_margin_deg=2,
        deadband_deg=0,
        ema_alpha=1,
        zones=[
            Zone(id="left", name="Left", azimuth=0, elevation=45, azimuth_range=(330, 30),
                 elevation_range=(30, 60), presets={"cam": "1"}),
            Zone(id="right", name="Right", azimuth=180, elevation=45, azimuth_range=(150, 210),
                 elevation_range=(30, 60), presets={"cam": "2"}),
        ],
    )
    left, right = settings.zones
    assert zone_contains(left, (359, 45), 2)
    assert zone_contains(left, (1, 58), 2)
    assert not zone_contains(left, (180, 45), 2)
    assert not zone_contains(left, (0, 31), 2)

    engine = TrackingFilter(settings)
    first = feed_for(engine, 0, .5, 359)
    second = feed_for(engine, .6, 1.2, 180)
    assert [move["zone"] for move in first + second] == ["left", "right"]


def test_coordinate_ranges_validate_pairing_and_center():
    with pytest.raises(ValidationError, match="both azimuth and elevation"):
        Zone(id="bad", name="Bad", azimuth=0, elevation=45, azimuth_range=(330, 30))
    with pytest.raises(ValidationError, match="center must be inside"):
        Zone(id="bad", name="Bad", azimuth=100, elevation=45, azimuth_range=(330, 30),
             elevation_range=(30, 60))


def test_cooldown_settle_and_rate_limit_count_each_camera():
    f = TrackingFilter(config(ema_alpha=1, settle_ms=5000, max_moves_per_min=2))
    assert len(feed_for(f, 0, 1)) == 1
    assert not feed_for(f, 1.1, 5.9, 180)
    assert feed_for(f, 6, 6.1, 180)[0]["zone"] == "two"
    assert not feed_for(f, 6.2, 12, 0)
    assert f.reason == "ignored: moves per minute limit"


def test_silence_and_unknown_required_signal_and_far_end():
    f = TrackingFilter(config(level_gate=True))
    for i in range(20):
        f.feed(event(level=-30), i / 10)
    assert f.current == "one"
    for i in range(20, 130):
        f.feed(event(level=-80), i / 10)
        f.tick(i / 10)
    assert f.current == "wide"
    assert f.feed(event(180), 13) is None
    assert "unavailable" in f.reason
    assert f.feed(event(180, level=-30, beam_freeze=True), 14) is None
    assert "far-end" in f.reason


def test_duplicate_invalid_gap_and_deadband():
    f = TrackingFilter(config())
    f.feed(event(time=1), 0)
    assert f.feed(event(time=1), .5) is None
    assert "duplicate" in f.reason
    assert f.feed(event(400, time=2), .6) is None
    assert f.candidate is None
    f.feed(event(time=3), 1)
    f.feed(event(time=4), 3)
    assert f.current is None  # a gap must restart confirmation
    with pytest.raises(ValidationError):
        config(elevation_min=80, elevation_max=20)
    with pytest.raises(ValidationError):
        config(ema_alpha=float("nan"))


async def test_api_validation_persistence_and_dry_default(client, runtime):
    runtime.settings.cameras = [Camera(id="cam", ip="127.0.0.1")]
    r = await client.put('/api/tracking/settings', json=config().model_dump())
    assert r.status_code == 200
    assert runtime.store.load().tracking.zones[0].name == "Seat 1"
    assert not runtime.follow.live
    assert (await client.post('/api/tracking/mode', json={"live": True})).status_code == 400
    assert (await client.put('/api/tracking/settings', json={"ema_alpha": 2})).status_code == 422
    for asset in ['tune.js', 'tracker.css', 'ordain-logo.png', 'ordain-logo-light.png']:
        assert (await client.get('/static/' + asset)).status_code == 200


async def test_mic_filter_to_mock_visca_and_control_switch(runtime):
    packets = []

    class MockVisca(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            packets.append(data)
            self.transport.sendto(b'\x90\x41\xff', addr)  # Raw VISCA on this ephemeral non-Sony port.

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(MockVisca, local_addr=('127.0.0.1', 0))
    camera = Camera(id="cam", ip="127.0.0.1", control_enabled=True, control_protocol="visca", visca_port=transport.get_extra_info('sockname')[1])
    runtime.settings.cameras = [camera]
    runtime.settings.tracking = config(confirm_ms=100)
    runtime.follow.configure(runtime.settings.tracking)
    runtime.follow.live = True
    parser = PositionParser(MicSettings())
    parser.on_sample = runtime.follow.feed
    try:
        for _ in range(4):
            parser.feed(json.dumps({"/api/audio/inputs/microphone/beam/direction": event()}).encode())
            await asyncio.sleep(.06)
        if runtime.follow.task:
            await runtime.follow.task
        assert len(packets) == 1
        assert packets[0] == bytes.fromhex('8101043f0201ff')
        assert runtime.follow.actual_moves
        camera.control_enabled = False
        with pytest.raises(ValueError, match="Enable"):
            await ViscaIpDriver(camera).recall('1')
        camera.control_enabled = True
        for token in ['-1', '128', 'save']:
            with pytest.raises(ValueError):
                await ViscaIpDriver(camera).recall(token)
        assert len(packets) == 1
    finally:
        transport.close()


async def test_error_stop_and_replay_never_recall(runtime, monkeypatch):
    runtime.settings.tracking = config()
    runtime.follow.configure(runtime.settings.tracking)
    calls = []

    async def fail(*args):
        calls.append(args)
        raise ValueError('Camera offline')

    monkeypatch.setattr(runtime.follow, 'recall', fail)
    runtime.follow.live = True
    d = {"zone": "one", "presets": {"cam": "1"}}
    for _ in range(3):
        await runtime.follow.execute(d, runtime.follow.generation)
    assert not runtime.follow.live
    rows = [{"t": i / 10, "event": event()} for i in range(15)]
    await runtime.follow.replay(rows, 8)
    assert len(calls) == 3
    assert len(runtime.follow.replay_state['decisions']) == 1
    assert not runtime.follow.replay_state['running']


async def test_continuous_silent_samples_return_wide_in_replay(runtime):
    runtime.settings.tracking = config()
    rows = tracking_scenario('jitter', [(0, 45), (180, 45)])
    await runtime.follow.replay(rows, 8)
    assert [d['zone'] for d in runtime.follow.replay_state['decisions']] == ['one', 'two', 'wide']
    assert not runtime.follow.actual_moves


async def test_only_real_cameras_survive_old_demo_profile(tmp_path):
    root = tmp_path / 'old-demo'
    store = Store(root)
    settings = store.load()
    settings.cameras = [Camera(id='demo1', ip='127.0.0.1', demo=True),
                        Camera(id='real', ip='192.168.1.20', name='Room camera')]
    settings.display.cameras = ['demo1', 'real']
    settings.tracking = config()
    settings.tracking.zones[0].presets = {'demo1': '1', 'real': '2'}
    settings.tracking.wide_presets = {'demo1': '0', 'real': '3'}
    store.save(settings)
    store.close()
    rt = Runtime(root, demo=True)
    try:
        assert [c.id for c in rt.settings.cameras] == ['real']
        assert [c['id'] for c in rt.snapshot()['cameras']] == ['real']
        assert rt.settings.display.cameras == ['real']
        assert rt.settings.tracking.zones[0].presets == {'real': '2'}
        assert rt.settings.tracking.wide_presets == {'real': '3'}
        assert all(not c.demo for c in rt.store.load().cameras)
    finally:
        await rt.close()
    empty = Runtime(tmp_path / 'new-demo', demo=True)
    try:
        assert not empty.settings.cameras
    finally:
        await empty.close()
