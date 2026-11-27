"""Expose OmniTrack camera identities to the microphone-only seat editor."""
import ipaddress
import json
from pathlib import Path


CAMERA_FILE = Path(__file__).parent / 'camera_config.json'


def camera_catalog(existing=(), path=None):
    path = CAMERA_FILE if path is None else Path(path)
    if not path.exists():
        return []
    config = json.loads(path.read_text(encoding='utf-8'))
    occupied_ips = {camera.get('ip') for camera in existing}
    occupied_ids = {camera.get('id') for camera in existing}
    cameras = []
    for key, camera in config.get('camera_config', {}).items():
        if not key.startswith('camera_') or not isinstance(camera, dict):
            continue
        try:
            ip = str(ipaddress.IPv4Address(camera.get('ip', '')))
        except ipaddress.AddressValueError:
            continue
        identity = 'omni_' + ip.replace('.', '_')
        if ip in occupied_ips or identity in occupied_ids:
            continue
        occupied_ips.add(ip)
        occupied_ids.add(identity)
        cameras.append({'id': identity, 'ip': ip,
                        'name': camera.get('cam_name') or key.replace('_', ' ').title(),
                        'control_enabled': True, 'preset_records': {}})
    return cameras
