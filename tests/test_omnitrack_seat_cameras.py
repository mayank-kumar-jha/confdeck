"""Seat assignments can reference cameras owned by the paired OmniTrack app."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'integration/current84/omnitrack-sennheiser/backend_services'))
import external_cameras
import driver_sennheiser
from confdeck import omnitrack_cameras


@pytest.fixture
def camera_file(tmp_path):
    path = tmp_path / 'camera_config.json'
    path.write_text(json.dumps({'camera_config': {
        'camera_1': {'ip': '192.168.1.106', 'cam_name': 'Room camera', 'password': 'secret'},
        'camera_2': {'ip': '192.168.1.109', 'cam_name': 'Camera 2'},
        'camera_3': {'ip': '192.168.1.108', 'cam_name': 'Camera 3'},
        'camera_4': {'ip': ''},
    }}), encoding='utf8')
    return path


def test_catalog_is_stable_private_and_deduplicated(camera_file):
    existing = [{'id': 'existing', 'ip': '192.168.1.106'}]
    cameras = external_cameras.camera_catalog(existing, camera_file)
    assert cameras == omnitrack_cameras.camera_catalog(existing, camera_file)
    assert [c['id'] for c in cameras] == ['omni_192_168_1_109', 'omni_192_168_1_108']
    assert 'secret' not in json.dumps(cameras)


async def test_seat_assignment_accepts_only_configured_external_cameras(camera_file, monkeypatch, client):
    monkeypatch.setenv('CONFDECK_OMNITRACK_CAMERA_CONFIG', str(camera_file))
    state = (await client.get('/api/state')).json()
    settings = state['settings']['tracking']
    settings['zones'] = [{'id': 'seat1', 'name': 'Seat 1', 'azimuth': 20,
                          'elevation': 30, 'radius': 25,
                          'presets': {'omni_192_168_1_109': '1'}}]
    response = await client.put('/api/tracking/settings', json=settings)
    assert response.status_code == 200, response.text
    saved = (await client.get('/api/state')).json()['settings']['tracking']['zones']
    assert saved[0]['presets'] == {'omni_192_168_1_109': '1'}
    settings['zones'][0]['presets'] = {'omni_192_168_1_111': '1'}
    assert (await client.put('/api/tracking/settings', json=settings)).status_code == 400


def test_driver_translates_external_camera_to_omnitrack_mapping(camera_file, tmp_path, monkeypatch):
    from core import config, state
    mapping_file = tmp_path / 'mapping.json'
    monkeypatch.setattr(config, 'CAMERA_CONFIG_FILE', str(camera_file))
    monkeypatch.setattr(config, 'MAPPING_FILE', str(mapping_file))
    monkeypatch.setattr(external_cameras, 'CAMERA_FILE', camera_file)
    monkeypatch.setattr(driver_sennheiser, 'request_confdeck', lambda path: {'settings': {
        'cameras': [], 'tracking': {'zones': [{'id': 'seat1', 'name': 'Seat 1',
            'presets': {'omni_192_168_1_109': '1'}}], 'wide_presets': {}}}})
    for name in ('mic_to_camera', 'mic_to_preset', 'mic_to_name'):
        monkeypatch.setattr(state, name, {})
    driver_sennheiser.ControllerDriver().get_all_seats()
    saved = json.loads(mapping_file.read_text())
    assert saved == [{'serial': 'sennheiser:seat1', 'person_name': 'Seat 1', 'camera': '2', 'preset': '1'}]
