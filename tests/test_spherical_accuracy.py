import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from confdeck.evaluation.assistants import propose_exclusions, settings_ab
from confdeck.evaluation.metrics import angular, metrics
from confdeck.evaluation.replay import replay
from confdeck.evaluation.synthetic import generate
from confdeck.tracking import TrackingSettings, Zone
from confdeck.tracking_math.config import Convention
from confdeck.tracking_math.engine import SphericalFilter
from confdeck.tracking_math.geometry import angles, distance, estimate_offset, nadir_check, vector
from confdeck.tracking_math.model import calibrate, fit, tolerance_kappa
from confdeck.vision.motion import MotionEnergy, learn_llr, llr, phase_correlation


def test_spherical_wrap_pole_and_conventions():
    assert distance(vector(359, 0), vector(1, 0)) == pytest.approx(2)
    assert distance(vector(0, 90), vector(180, 90)) == 0
    assert angular((0, 0), (180, 0)) == 180
    convention = Convention(clockwise=True, elevation_reference='vertical', orientation_offset=30)
    assert angles(vector(42, 28, convention), convention) == pytest.approx((42, 28))
    assert nadir_check([(123, 0)]*10, convention)['passed']
    assert not nadir_check([(123, 90)]*10, convention)['passed']
    assert estimate_offset([350, 170], [10, 190]) == pytest.approx(20)
    assert tolerance_kappa(10) > tolerance_kappa(30)


def test_robust_ghost_clusters_and_held_out_separability():
    rng = np.random.default_rng(3)
    points = np.array([vector(20, 30)]*80+[vector(200, 30)]*20)+rng.normal(0, .01, (100, 3))
    components = fit(points)
    assert len(components) == 2
    assert sum(c['weight'] for c in components) == pytest.approx(1)
    assert all(c['kappa'] > 100 for c in components)
    session, settings = generate(tag='calibration')
    result = calibrate(session, settings.zones, Convention())
    assert set(result['separability']) == {'a', 'b'}
    assert all(v > .85 for v in result['separability'].values())
    session['tag'] = 'test'
    with pytest.raises(ValueError, match='Only calibration'):
        calibrate(session, settings.zones, Convention())


@pytest.mark.parametrize('seed', [7, 19, 41])
def test_new_engine_accuracy_regression(seed):
    session, settings = generate(seed)
    old = metrics(session, replay(session, settings), settings)
    run = replay(session, settings, 'new')
    new = metrics(session, run, settings)
    assert run == replay(session, settings, 'new')
    assert new['correct_rate'] >= .80
    assert new['wrong_rate'] < old['wrong_rate']/2
    assert new['spurious_switches_per_min'] <= .3
    assert new['switch_latency_median'] <= 1.5
    assert new['echo_trigger_trial_rate'] == 0


def test_quality_gates_and_none_decay():
    _, settings = generate()
    engine = SphericalFilter(settings)
    def feed(t, a=20, e=20, level=-30, **kw):
        return engine.feed({'azimuth': a, 'elevation': e, 'level': level, **kw}, t)
    feed(0)
    assert 'spike' in engine.reason
    for n in range(1, 20):
        feed(n/10, a=20+(n%3)*.1)
    assert engine.current == 'a'
    feed(2, a=200)
    assert engine.gate.last_reason == 'implausible jump'
    feed(2.1, a=200.1)
    assert engine.gate.last_reason == 'corroborating jump'
    feed(2.2, a=315, e=90)
    assert engine.gate.last_reason == 'far-end / parked beam'
    feed(2.3, a=float('nan'))
    assert 'invalid' in engine.reason
    for n in range(24, 50):
        engine.tick(n/10)
    assert engine.current is None
    assert engine.posterior[-1] > .8


def test_frozen_high_energy_and_low_noise():
    _, settings = generate()
    gate = SphericalFilter(settings).gate
    for n in range(55):
        gate.feed({'azimuth': 20, 'elevation': 20, 'level': -30}, n/10)
    assert gate.last_reason == 'frozen high-level beam'
    reliability, reason, _ = gate.feed({'azimuth': 25, 'elevation': 20, 'level': -80}, 6)
    assert reliability == 0 and reason == 'below noise floor'


def test_stationary_speech_levels_not_rejected_as_frozen():
    _, settings = generate()
    gate = SphericalFilter(settings).gate
    levels = [-32, -28, -35, -26, -40, -30]
    for n in range(60):
        gate.feed({'azimuth': 20, 'elevation': 20, 'level': levels[n % len(levels)]}, n/10)
    assert gate.last_reason == 'accepted'


def test_time_weighted_metrics_not_sample_weighted():
    settings = TrackingSettings(zones=[Zone(id='a', name='A', azimuth=0, elevation=0), Zone(id='b', name='B', azimuth=180, elevation=0)])
    session = {'labels': [{'start': 0, 'end': 10, 'seat': 'a', 'condition': 'normal'}]}
    result = {'engine': 'test', 'points': [{'t': 0, 'seat': 'a'}, {'t': 8, 'seat': 'b'}, {'t': 9, 'seat': None}, {'t': 10, 'seat': None}], 'commands': []}
    score = metrics(session, result, settings)
    assert score['correct_rate'] == .8
    assert score['wrong_rate'] == .1
    assert score['no_decision_rate'] == .1
    assert score['opposite_rate'] == .1
    assert score['confusion_seconds'][0] == [8, 1, 1]


def test_vision_motion_lighting_mask_budget_and_phase():
    rois = {'a': {'rect': [0, 0, 80, 80], 'masks': []}}
    frame = np.zeros((180, 320), dtype=np.float32)
    motion = MotionEnergy()
    assert motion.process(frame, rois, 0) == {}
    assert motion.process(frame+30, rois, .2)['a'] == 0
    frame[:80, :80] = 80
    assert motion.process(frame, rois, .4)['a'] > 1
    assert motion.process(frame, rois, .6, blocked=True) == {}
    assert motion.process(frame, rois, .8) == {}
    model = learn_llr([5]*10, [0]*10)
    assert llr(5, model) > 0 and llr(0, model) < 0
    texture = np.random.default_rng(7).normal(100, 30, (180, 320))
    moved = phase_correlation(texture, np.roll(texture, (4, 8), axis=(0, 1)))
    assert moved['moved'] and moved['shift'] == [-4, -8]
    assert phase_correlation(texture, texture)['moved'] is False
    assert phase_correlation(np.zeros((10, 10)), np.zeros((10, 10)))['moved'] is None


def test_off_imports_no_vision_modules():
    code = 'import sys; from confdeck.evaluation.replay import make_engine; from confdeck.tracking import TrackingSettings; make_engine(TrackingSettings(), "new"); assert not any(k.startswith("confdeck.vision") or k.startswith("mediapipe") for k in sys.modules)'
    subprocess.run([sys.executable, '-c', code], check=True)


async def test_ab_restores_after_failure_and_requires_confirmation():
    device = SimpleNamespace(read=AsyncMock(return_value={'values': {'beam': {'setting': 1}}, 'unavailable': {}}),
                             resources={'beam': {'schema': {'type': 'object', 'properties': {'setting': {'type': 'integer'}}}}}, apply=AsyncMock())
    with pytest.raises(ValueError, match='Confirm'):
        await settings_ab(device, [{}, {}], AsyncMock())
    runner = AsyncMock(side_effect=ValueError('trial failed'))
    with pytest.raises(ValueError, match='trial failed'):
        await settings_ab(device, [{'beam': {'setting': 2}}, {'beam': {'setting': 3}}], runner, True)
    assert device.apply.call_args.kwargs['confirmed']
    assert device.apply.call_args.args[0] == {'beam': {'setting': 1}}


async def test_lab_routes_and_recording(client, runtime):
    _, settings = generate()
    runtime.settings.tracking = settings
    runtime.follow.configure(settings)
    assert (await client.post('/api/accuracy/session', json={'action': 'start', 'tag': 'test'})).status_code == 200
    assert (await client.post('/api/accuracy/trial', json={'action': 'start', 'seat': 'a', 'condition': 'normal'})).status_code == 200
    runtime.follow.feed({'azimuth': 20, 'elevation': 20, 'level': -30})
    await client.post('/api/accuracy/session', json={'action': 'stop'})
    data = (await client.get('/api/accuracy/session')).json()
    assert any(r['kind'] == 'decision' for r in data['rows'])
    assert len(data['labels']) == 1
    assert (await client.post('/api/accuracy/vision/frame', json={})).json()['mode'] == 'off'
    assert (await client.get('/static/accuracy-lab.js')).status_code == 200
    assert (await client.post('/api/accuracy/profile', json={'profile': {'engine': 'new'}})).status_code >= 400
    assert (await client.post('/api/accuracy/profile', json={'profile': {'engine': 'new'}, 'confirm': True})).status_code == 200


def test_exclusion_proposals_ignore_parked_signature():
    session, _ = generate()
    assert propose_exclusions(session)['proposals'] == []
