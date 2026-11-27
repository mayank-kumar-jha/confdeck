import importlib.util
from pathlib import Path

import pytest

from confdeck.models import Camera, Settings

path = Path(__file__).parents[1] / 'integration/current84/restore_pre_reset_seats.py'
spec = importlib.util.spec_from_file_location('restore_pre_reset_seats', path)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def test_restores_by_ip_and_preserves_physical_azimuth():
    previous = Settings().model_dump(mode='json')
    current = Settings().model_dump(mode='json')
    previous['mic']['azimuth_offset'] = 37
    previous['cameras'] = [Camera(id='old108', ip='192.168.1.108').model_dump(mode='json'),
                           Camera(id='old112', ip='192.168.1.112').model_dump(mode='json')]
    previous['tracking']['zones'] = [{'id': 'seat-a', 'name': 'Kashif', 'azimuth': 78.8,
                                      'elevation': 57.3, 'radius': 25, 'presets': {'old108': '2'}}]
    previous['scenes'] = [{'id': 'scene-a', 'name': 'Camera 112', 'presets': {'old112': '25'}}]
    cameras = {'camera_config': {'camera_2': {'ip': '192.168.1.109'}, 'camera_3': {'ip': '192.168.1.108'}}}
    settings, seats, mappings, unresolved = restore.prepare(previous, current, cameras)
    assert settings['tracking']['zones'][0]['azimuth'] == pytest.approx(41.8)
    assert settings['tracking']['zones'][0]['presets'] == {'omni_192_168_1_108': '2'}
    assert mappings[0]['camera'] == '3' and mappings[0]['preset'] == '2'
    assert seats[0]['name'] == 'Kashif'
    assert unresolved == ['192.168.1.112']


def test_rejects_unconfigured_camera_and_different_microphone():
    previous = Settings().model_dump(mode='json')
    current = Settings().model_dump(mode='json')
    previous['cameras'] = [Camera(id='old108', ip='192.168.1.108').model_dump(mode='json')]
    previous['tracking']['zones'] = [{'id': 'seat-a', 'name': 'Seat', 'azimuth': 0,
                                      'elevation': 45, 'radius': 25, 'presets': {'old108': '1'}}]
    with pytest.raises(ValueError, match='not configured'):
        restore.prepare(previous, current, {'camera_config': {}})
    current['mic']['ip'] = '192.168.1.99'
    with pytest.raises(ValueError, match='microphone differs'):
        restore.prepare(previous, current, {'camera_config': {}})
