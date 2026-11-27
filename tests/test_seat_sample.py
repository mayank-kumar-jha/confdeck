from types import SimpleNamespace

import pytest

from confdeck.evaluation import seat_sample
from confdeck.models import MicSettings
from confdeck.tracking import TrackingSettings, Zone


@pytest.fixture
def setup(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(seat_sample.time, 'monotonic', lambda: clock[0])
    tracking = TrackingSettings()
    follow = SimpleNamespace(live=False, lab=SimpleNamespace(recorder=None), stop=lambda reason: None)
    runtime = SimpleNamespace(settings=SimpleNamespace(tracking=tracking, mic=MicSettings(), microphones=[]),
                              follow=follow, microphones={'primary': object()})
    sample = seat_sample.SeatSample(runtime)
    return sample, clock, runtime


def collect(sample, clock, angle=lambda i: 30, level=-40):
    for i in range(80):
        clock[0] = 100 + i * .25
        sample.feed({'azimuth': angle(i), 'elevation': 30, 'level': level}, clock[0])
    clock[0] = 120
    return sample.snapshot()


def test_sample_creates_reviewable_direction_and_reports_level(setup):
    sample, clock, runtime = setup
    runtime.settings.tracking.zones.append(Zone(id='other', name='Other', azimuth=32, elevation=30))
    sample.start('Chair', 'primary')
    result = collect(sample, clock, lambda i: 30 + i % 3 - 1)
    assert result['status'] == 'ready'
    assert result['proposal']['name'] == 'Chair'
    assert result['proposal']['azimuth'] == pytest.approx(30, abs=1)
    assert result['quality']['seconds_with_readings'] == 20
    assert result['quality']['median_level'] == -40
    assert any('similar direction' in w for w in result['warnings'])
    assert not any('samples' in key for key in result)
    assert len(runtime.settings.tracking.zones) == 1


def test_missing_level_is_allowed_but_disclosed(setup):
    sample, clock, _ = setup
    sample.start('Chair', 'primary')
    result = collect(sample, clock, level=None)
    assert result['status'] == 'ready'
    assert result['quality']['median_level'] is None
    assert any('unavailable' in w for w in result['warnings'])


def test_sparse_frozen_and_ambiguous_samples_require_retry(setup):
    sample, clock, _ = setup
    sample.start('Chair', 'primary')
    for i in range(80):
        sample.feed({'azimuth': 30, 'elevation': 30, 'beam_freeze': True}, 100+i*.25)
    clock[0] = 120
    assert sample.snapshot()['status'] == 'retry'
    clock[0] = 100
    sample.start('Chair', 'primary')
    result = collect(sample, clock, lambda i: 30 if i % 2 else 210)
    assert result['status'] == 'retry'


def test_requires_connection_and_rejects_setting_changes(setup):
    sample, clock, runtime = setup
    with pytest.raises(ValueError, match='Connect'):
        sample.start('Chair', 'other')
    sample.start('Chair', 'primary')
    runtime.settings.mic.azimuth_offset = 10
    clock[0] = 120
    assert sample.snapshot()['status'] == 'retry'


def test_excludes_other_sources_and_below_threshold(setup):
    sample, clock, _ = setup
    sample.start('Chair', 'primary')
    for i in range(80):
        sample.feed({'mic_id': 'other', 'azimuth': 30, 'elevation': 30}, 100+i*.25)
        sample.feed({'azimuth': 30, 'elevation': 30, 'level': -80}, 100+i*.25)
    clock[0] = 120
    assert sample.snapshot()['status'] == 'retry'

async def test_seat_sample_api_saves_only_after_review(runtime, client):
    runtime.microphones['primary'] = object()
    try:
        response = await client.post('/api/accuracy/seat-sample', json={'action': 'start', 'name': 'Chair'})
        assert response.status_code == 200
        assert (await client.post('/api/accuracy/seat-sample', json={'action': 'save'})).status_code == 400
        sample = runtime.follow.lab.seat_sample
        sample.state['start'] -= 20
        start = sample.state['start']
        for i in range(80):
            sample.feed({'azimuth': 30, 'elevation': 30, 'level': -40}, start+i*.25)
        response = await client.get('/api/accuracy/seat-sample')
        assert response.json()['status'] == 'ready'
        assert not runtime.settings.tracking.zones
        response = await client.post('/api/accuracy/seat-sample', json={'action': 'save'})
        assert response.status_code == 200
        assert runtime.settings.tracking.zones[0].name == 'Chair'
        assert runtime.settings.tracking.zones[0].presets == {}
        assert (await client.post('/api/accuracy/seat-sample', json={'action': 'save'})).status_code == 400
    finally:
        runtime.microphones.pop('primary', None)


def test_shared_sample_creates_one_seat_with_two_distinct_directions(setup):
    from confdeck.models import AdditionalMic
    sample, clock, runtime = setup
    runtime.settings.microphones.append(AdditionalMic(id='rear', ip='192.168.1.93'))
    runtime.microphones['rear'] = object()
    sample.start('Shared chair', '__all__')
    for i in range(80):
        clock[0] = 100+i*.25
        for source, direction in [('primary', 30), ('rear', 215)]:
            sample.feed({'mic_id': source, 'sample_time': clock[0], 'azimuth': direction,
                         'elevation': 30, 'level': -40}, clock[0])
    clock[0] = 120
    result = sample.snapshot()
    assert result['status'] == 'ready'
    assert result['proposal']['azimuth'] == pytest.approx(30)
    assert result['proposal']['microphone_profiles']['rear']['azimuth'] == pytest.approx(215)
    assert set(result['per_microphone']) == {'primary', 'rear'}
    assert any('speech evidence' in warning for warning in result['warnings'])
    assert not runtime.settings.tracking.zones


def test_shared_sample_fails_if_one_source_has_no_evidence(setup):
    from confdeck.models import AdditionalMic
    sample, clock, runtime = setup
    runtime.settings.microphones.append(AdditionalMic(id='rear', ip='192.168.1.93'))
    runtime.microphones['rear'] = object()
    sample.start('Shared chair', '__all__')
    assert collect(sample, clock)['status'] == 'retry'


def test_sample_rejects_duplicate_and_stale_direction_readings(setup):
    sample, clock, _ = setup
    sample.start('Chair', 'primary')
    for i in range(80):
        clock[0] = 100+i*.25
        sample.feed({'sample_time': 100, 'azimuth': 30, 'elevation': 30, 'level': -40}, clock[0])
    clock[0] = 120
    assert sample.snapshot()['status'] == 'retry'


async def test_shared_sample_api_persists_one_seat_and_engine(runtime, client):
    from confdeck.models import AdditionalMic
    runtime.settings.microphones = [AdditionalMic(id='rear', ip='192.168.1.93')]
    runtime.microphones.update({'primary': object(), 'rear': object()})
    try:
        response = await client.post('/api/accuracy/seat-sample', json={'action': 'start', 'name': 'Chair', 'mic_id': '__all__'})
        assert response.status_code == 200
        sample = runtime.follow.lab.seat_sample
        sample.state['start'] -= 20
        start = sample.state['start']
        for i in range(80):
            for source, angle in [('primary', 30), ('rear', 210)]:
                sample.feed({'mic_id': source, 'azimuth': angle, 'elevation': 30, 'level': -40}, start+i*.25)
        assert (await client.get('/api/accuracy/seat-sample')).json()['status'] == 'ready'
        response = await client.post('/api/accuracy/seat-sample', json={'action': 'save'})
        assert response.status_code == 200, response.text
        assert len(runtime.settings.tracking.zones) == 1
        assert 'rear' in runtime.settings.tracking.zones[0].microphone_profiles
        assert set(runtime.follow.engine.sources) == {'primary', 'rear'}
        reloaded = runtime.store.load().tracking.zones[0]
        assert reloaded.microphone_profiles['rear'].azimuth == pytest.approx(210)
        with pytest.raises(ValueError, match='spherical engine'):
            runtime.follow.mode(True)
    finally:
        runtime.microphones.clear()
