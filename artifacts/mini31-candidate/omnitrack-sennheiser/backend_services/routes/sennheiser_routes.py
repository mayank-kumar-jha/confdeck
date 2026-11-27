"""Bounded same-origin interface for the Sennheiser setup panel."""
import json
import re
from flask import Blueprint, request, jsonify
from external_cameras import camera_catalog
from core import state

import base64
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from services.ptz_service import get_camera_info, move_camera, stop_camera, zoom_camera

sennheiser_bp = Blueprint('sennheiser', __name__)
_PORT4000_VISION_LOCAL_ONLY = True
ALLOWED = {
    ('GET', 'state'),
    ('GET', 'tracking'),
    ('POST', 'tracking/mode'),
    ('PUT', 'tracking/settings'),
    ('POST', 'mic'),
    ('GET', 'mic/device-settings'),
    ('POST', 'mic/device-settings'),
    ('POST', 'mic/device-settings/revert'),
    ('POST', 'mic/notice/dismiss'),
    ('GET', 'cameras'),
}

_VISION_LOCK = threading.Lock()
_VISION_CACHE = {
    'expires': 0.0,
    'alias_to_id': {},
    'cd_ids': set(),
    'cd_cameras': [],
    'vision_cfg': {'mode': 'off', 'camera_modes': {}, 'rois': []},
    'autotrack': {'enabled': False, 'camera': '', 'mode': 'off'},
}
_VISION_STATES = {}
_LAST_CLIENT_FRAME = {}
_MOVING_UNTIL = {}
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_LOCAL_ENGINE_LOCK = threading.Lock()
_LOCAL_ENGINES = {}
_LOCAL_ENGINE_LOCKS = {}
_LOCAL_VISION_SETTINGS_LOCK = threading.RLock()
_LOCAL_VISION_SETTINGS = None
_LOCAL_AUTOTRACK_LOCK = threading.RLock()
_LOCAL_AUTOTRACK = {'enabled': False, 'camera': '', 'mode': 'off'}
_LOCAL_TRACKING_RUNTIME = {}
_LOCAL_TRACKING_STOP_TIMERS = {}
_LOCAL_TRACKING_SMOOTHING_ALPHA = 0.45
_LOCAL_TRACKING_REVERSE_CONFIRMATIONS = 2
_LOCAL_TRACKING_LEASE_SECONDS = 0.45
_LOCAL_TRACKING_REFRESH_SECONDS = 0.22
_LOCAL_TRACKING_SETTLE_SECONDS = 0.20
_LOCAL_TRACKING_LOST_GRACE_SECONDS = 0.30
_RTSP_READERS_LOCK = threading.Lock()
_RTSP_READERS = {}
_ACTIVE_FORWARD_LOCK = threading.Lock()
_ACTIVE_FORWARD_PENDING = {}
_ACTIVE_FORWARD_RUNNING = set()
_POST_PRESET_UNTIL = {}


def _omnitrack_root() -> Path:
    candidates = []
    env_dir = os.environ.get('OMNITRACK_DIR', '').strip()
    if env_dir:
        p = Path(env_dir)
        candidates.extend([p, p / 'backend_services'])
    env_cfg = os.environ.get('CONFDECK_OMNITRACK_CAMERA_CONFIG', '').strip()
    if env_cfg:
        candidates.append(Path(env_cfg).parent)
    here = Path(__file__).resolve()
    if len(here.parents) >= 2:
        candidates.append(here.parents[1])  # backend_services/
    candidates.append(Path.cwd())
    if len(here.parents) >= 3:
        candidates.append(here.parents[2])
    for path in candidates:
        if (path / 'camera_config.json').is_file() or (path / 'camera_rtsp.json').is_file():
            return path
    return here.parents[1] if len(here.parents) >= 2 else Path.cwd()


def _read_json_file(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _load_omnitrack_cameras() -> list[dict]:
    root = _omnitrack_root()
    raw_cfg = _read_json_file(root / 'camera_config.json', {})
    raw_rtsp = _read_json_file(root / 'camera_rtsp.json', {})
    cameras = []
    if not isinstance(raw_cfg, dict):
        raw_cfg = {}
    if not isinstance(raw_rtsp, dict):
        raw_rtsp = {}
    cfg = raw_cfg.get('camera_config') if isinstance(raw_cfg.get('camera_config'), dict) else raw_cfg
    ip_fallback = raw_cfg.get('camera_ips') if isinstance(raw_cfg.get('camera_ips'), dict) else {}
    brand_fallback = raw_cfg.get('camera_brands') if isinstance(raw_cfg.get('camera_brands'), dict) else {}
    rtsp = raw_rtsp
    keys = list(cfg.keys()) + [k for k in rtsp.keys() if k not in cfg]
    for key in keys:
        c_raw = cfg.get(key)
        r_raw = rtsp.get(key)
        if not isinstance(c_raw, dict) and not isinstance(r_raw, dict):
            continue
        c = c_raw if isinstance(c_raw, dict) else {}
        r = r_raw if isinstance(r_raw, dict) else {}
        ip = str(c.get('ip') or ip_fallback.get(key) or '').strip()
        orig_rtsp = str(c.get('rtsp_url') or r.get('rtsp_url') or r.get('original') or '').strip()
        if not ip and orig_rtsp.startswith('rtsp://'):
            host_part = orig_rtsp.split('://', 1)[1].split('/', 1)[0].split('@')[-1]
            ip = host_part.split(':', 1)[0]
        local_rtsp = str(r.get('local') or (f'rtsp://127.0.0.1:8554/{key}' if key else '')).strip()
        cameras.append({
            'key': key,
            'name': str(c.get('cam_name') or c.get('name') or r.get('name') or key),
            'ip': ip,
            'protocol': str(c.get('protocol') or c.get('brand') or brand_fallback.get(key) or ''),
            'brand': str(c.get('brand') or brand_fallback.get(key) or ''),
            'enabled': bool(c.get('enabled', True)),
            'rtsp_url': orig_rtsp,
            'local_rtsp': local_rtsp,
        })
    return cameras


def _load_omnitrack_seats(mapping_path: Path | None = None) -> list[dict]:
    root = _omnitrack_root()
    data = _read_json_file(mapping_path or (root / 'mapping_data.json'), [])
    seats = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            label = str(row.get('person_name') or row.get('label') or row.get('seat_name') or row.get('seat_id') or '').strip()
            sid = str(row.get('seat_name') or row.get('seat_id') or row.get('id') or '').strip()
            if not sid and row.get('serial'):
                sid = str(row['serial']).split(':', 1)[-1].strip()
            sid = sid or label
            if not sid:
                continue
            seats.append({
                'id': label or sid,
                'seat_id': sid,
                'label': label or sid,
                'preset': str(row.get('preset') or row.get('preset_token') or ''),
                'camera': str(row.get('camera') or row.get('camera_id') or ''),
            })
    return seats


def _load_local_vision_settings() -> dict:
    path = _omnitrack_root() / 'vision_4000.json'
    if not path.is_file():
        return {'mode': 'advisory', 'camera_modes': {}, 'rois': []}
    try:
        loaded = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as err:
        raise RuntimeError(f'Unable to read local vision settings from {path}: {err}') from err
    if not isinstance(loaded, dict):
        raise ValueError(f'Invalid local vision settings in {path}')

    mode = str(loaded.get('mode') or 'advisory')
    camera_modes = loaded.get('camera_modes') or {}
    rois = loaded.get('rois') or []
    if mode not in {'off', 'advisory', 'active'} or not isinstance(camera_modes, dict):
        raise ValueError(f'Invalid local vision settings in {path}')
    if not isinstance(rois, list) or any(not isinstance(roi, dict) for roi in rois):
        raise ValueError(f'Invalid local vision ROIs in {path}')

    return {
        'mode': mode,
        'camera_modes': {
            str(camera): str(value)
            for camera, value in camera_modes.items()
            if str(value) in {'off', 'advisory', 'active'}
        },
        'rois': [dict(roi) for roi in rois],
    }


def _call_confdeck(path: str, body=None, method: str = 'GET', timeout: float = 3):
    return {'error': 'Port 4000 does not proxy operations to ConfDeck HTTP.'}, 503


def _port4000_camera_settings() -> list[dict]:
    cameras = []
    for camera in _load_omnitrack_cameras():
        cameras.append({
            'id': camera['key'],
            'name': camera['name'],
            'ip': camera['ip'],
            'port': 554,
            'control_protocol': camera['protocol'],
            'control_enabled': camera['enabled'],
            'demo': False,
            'preset_records': {},
        })
    return cameras


def _local_microphone_operation(resource: str, method: str, body: dict, source: str | None):
    from services.port4000_mic_service import get_port4000_mic_service

    service = get_port4000_mic_service()
    try:
        if method == 'GET' and resource == 'state':
            result = service.snapshot()
            result['settings']['cameras'] = _port4000_camera_settings()
            return result, 200
        if method == 'GET' and resource == 'tracking':
            return service.tracking(), 200
        if method == 'PUT' and resource == 'tracking/settings':
            return service.save_tracking(body), 200
        if method == 'POST' and resource == 'tracking/mode':
            return service.set_tracking_mode(body), 200
        if method == 'POST' and resource == 'mic':
            return service.save_microphone(body), 200
        if method == 'GET' and resource == 'mic/device-settings':
            return service.read_device_settings(source or 'primary'), 200
        if method == 'POST' and resource == 'mic/device-settings':
            return service.write_device_settings(source or 'primary', body), 200
        if method == 'POST' and resource == 'mic/device-settings/revert':
            return service.write_device_settings(source or 'primary', body, revert=True), 200
        if method == 'POST' and resource == 'mic/notice/dismiss':
            return service.dismiss_mic_notice(), 200
        if method == 'GET' and resource == 'cameras':
            return _port4000_camera_settings(), 200
        if method == 'POST' and resource == 'microphones':
            return service.add_microphone(body), 200
        if resource.startswith('microphones/'):
            mic_id = resource.split('/', 1)[1]
            if not re.fullmatch(r'[a-z0-9_-]{1,40}', mic_id):
                return {'error': 'Invalid microphone selection'}, 400
            if method == 'PUT':
                return service.save_microphone(body, mic_id), 200
            if method == 'DELETE':
                return service.remove_microphone(mic_id), 200
        return {'error': 'This operation is not available in Port 4000 local mode'}, 501
    except ValueError as err:
        return {'error': str(err)}, 400
    except TimeoutError as err:
        return {'error': str(err)}, 504
    except (RuntimeError, OSError) as err:
        return {'error': str(err)}, 503


def _sync_cameras_with_confdeck(force: bool = False) -> dict:
    now = time.monotonic()
    with _VISION_LOCK:
        if not force and now < _VISION_CACHE['expires'] and _VISION_CACHE['alias_to_id']:
            return {
                'alias_to_id': dict(_VISION_CACHE['alias_to_id']),
                'cd_ids': set(_VISION_CACHE['cd_ids']),
                'cd_cameras': list(_VISION_CACHE['cd_cameras']),
                'vision_cfg': json.loads(json.dumps(_VISION_CACHE['vision_cfg'])),
                'autotrack': dict(_VISION_CACHE['autotrack']),
                'status': 200,
            }

    if _PORT4000_VISION_LOCAL_ONLY:
        local_cameras = _load_omnitrack_cameras()
        vision_cfg = _load_local_vision_settings()
        alias_to_id = {}
        for camera in local_cameras:
            cid = str(camera.get('key') or '').strip()
            ip = str(camera.get('ip') or '').strip()
            if not cid:
                continue
            alias_to_id[cid] = cid
            if ip:
                alias_to_id[ip] = cid
                alias_to_id[f"omni_{ip.replace('.', '_')}"] = cid

        autotrack = _local_autotrack_snapshot()
        with _VISION_LOCK:
            _VISION_CACHE['expires'] = time.monotonic() + 1.0
            _VISION_CACHE['alias_to_id'] = dict(alias_to_id)
            _VISION_CACHE['cd_ids'] = set()
            _VISION_CACHE['cd_cameras'] = []
            _VISION_CACHE['vision_cfg'] = dict(vision_cfg)
            _VISION_CACHE['autotrack'] = autotrack
        return {
            'alias_to_id': alias_to_id,
            'cd_ids': set(),
            'cd_cameras': [],
            'vision_cfg': vision_cfg,
            'autotrack': autotrack,
            'status': 200,
        }

    state_data, st_code = _call_confdeck('state', method='GET', timeout=3)
    auto_data, _ = _call_confdeck('accuracy/vision/autotrack', method='GET', timeout=3)
    omni_cams = _load_omnitrack_cameras()

    cd_cameras = []
    vision_cfg = {'mode': 'off', 'camera_modes': {}, 'rois': []}
    if st_code < 400 and isinstance(state_data, dict):
        settings = state_data.get('settings') or {}
        cd_cameras = list(settings.get('cameras') or [])
        vision_cfg = dict((settings.get('tracking') or {}).get('vision') or vision_cfg)

    alias_to_id = {}
    cd_ids = set()
    by_ip = {}
    for c in cd_cameras:
        if not isinstance(c, dict):
            continue
        cid = str(c.get('id') or '').strip()
        cip = str(c.get('ip') or '').strip()
        if not cid:
            continue
        cd_ids.add(cid)
        alias_to_id[cid] = cid
        if cip:
            by_ip[cip] = cid
            alias_to_id[cip] = cid
            alias_to_id[f"omni_{cip.replace('.', '_')}"] = cid

    for oc in omni_cams:
        key = oc['key']
        oip = oc.get('ip', '')
        cid = alias_to_id.get(key) or by_ip.get(oip) or key
        alias_to_id[key] = cid
        if oip:
            alias_to_id.setdefault(oip, cid)
            alias_to_id.setdefault(f"omni_{oip.replace('.', '_')}", cid)

    autotrack = auto_data if isinstance(auto_data, dict) else {'enabled': False, 'camera': '', 'mode': 'off'}
    with _VISION_LOCK:
        _VISION_CACHE['expires'] = time.monotonic() + (3.0 if st_code < 400 else 1.0)
        _VISION_CACHE['alias_to_id'] = dict(alias_to_id)
        _VISION_CACHE['cd_ids'] = set(cd_ids)
        _VISION_CACHE['cd_cameras'] = list(cd_cameras)
        _VISION_CACHE['vision_cfg'] = dict(vision_cfg)
        _VISION_CACHE['autotrack'] = dict(autotrack)

    return {
        'alias_to_id': alias_to_id,
        'cd_ids': cd_ids,
        'cd_cameras': cd_cameras,
        'vision_cfg': vision_cfg,
        'autotrack': autotrack,
        'status': st_code,
    }


def _resolve_camera_alias(identifier: str | None) -> str:
    raw = str(identifier or '').strip()
    if not raw:
        return ''
    with _VISION_LOCK:
        cached = _VISION_CACHE['alias_to_id'].get(raw)
    if cached:
        return cached
    synced = _sync_cameras_with_confdeck(force=True)
    return synced['alias_to_id'].get(raw, raw)


def _camera_keys(identifier: str | None, synced: dict | None = None) -> set[str]:
    raw = str(identifier or '').strip()
    if synced is None:
        synced = _sync_cameras_with_confdeck(force=False)
    cid = synced['alias_to_id'].get(raw, raw) if raw else ''
    keys = {k for k in (raw, cid) if k}
    for alias, mapped in synced['alias_to_id'].items():
        if mapped == cid:
            keys.add(alias)
    return keys


def _clamp_box(box) -> list[int]:
    x, y, w, h = [int(round(float(v))) for v in box]
    x = max(0, min(319, x))
    y = max(0, min(179, y))
    w = max(1, min(320 - x, w))
    h = max(1, min(180 - y, h))
    return [x, y, w, h]


def _normalize_roi_dict(raw: dict, fallback_cam: str, fallback_preset: str) -> dict:
    masks = [
        _clamp_box(m)
        for m in (raw.get('masks') or [])[:8]
        if isinstance(m, (list, tuple)) and len(m) == 4
    ]
    kind_raw = str(raw.get('kind') or 'seat').strip().lower()
    kind = 'teaching' if kind_raw in ('teaching', 'blackboard', 'board', 'presenter', 'teacher', 'teaching_zone') else 'seat'
    out = {
        'camera': _resolve_camera_alias(raw.get('camera') or fallback_cam),
        'preset': str(raw.get('preset', fallback_preset) or ''),
        'seat': str(raw.get('seat') or '').strip() or ('Teaching Zone' if kind == 'teaching' else 'Seat'),
        'rect': _clamp_box(raw.get('rect') or [0, 0, 1, 1]),
        'masks': masks,
        'kind': kind,
    }
    if isinstance(raw.get('face_signature'), list) and raw['face_signature']:
        out['face_signature'] = [float(v) for v in raw['face_signature'][:256]]
    if raw.get('signature_threshold') is not None:
        out['signature_threshold'] = float(raw['signature_threshold'])
    return out


def _effective_camera_mode(cid: str, vision_cfg: dict, keys: set[str] | None = None) -> str:
    gmode = str(vision_cfg.get('mode') or 'off')
    if gmode == 'off':
        return 'off'
    cam_modes = vision_cfg.get('camera_modes') or {}
    for k in [cid, *sorted(keys or set())]:
        if k in cam_modes:
            return str(cam_modes[k])
    return gmode


def _local_vision_config(synced: dict) -> dict:
    global _LOCAL_VISION_SETTINGS
    with _LOCAL_VISION_SETTINGS_LOCK:
        if _LOCAL_VISION_SETTINGS is None:
            path = _omnitrack_root() / 'vision_4000.json'
            if path.is_file() or _PORT4000_VISION_LOCAL_ONLY:
                _LOCAL_VISION_SETTINGS = _load_local_vision_settings()
            else:
                shared = synced.get('vision_cfg') or {}
                shared_mode = str(shared.get('mode') or 'off')
                _LOCAL_VISION_SETTINGS = {
                    'mode': shared_mode if shared_mode in {'advisory', 'active'} else 'advisory',
                    'camera_modes': {
                        str(camera): str(value)
                        for camera, value in (shared.get('camera_modes') or {}).items()
                        if str(value) in {'off', 'advisory', 'active'}
                    },
                    'rois': list(shared.get('rois') or []),
                }
                _write_local_vision_config_locked()

        return {
            **_LOCAL_VISION_SETTINGS,
            'camera_modes': dict(_LOCAL_VISION_SETTINGS['camera_modes']),
            'rois': list(
                _LOCAL_VISION_SETTINGS.get('rois', [])
                if _PORT4000_VISION_LOCAL_ONLY
                else (synced.get('vision_cfg') or {}).get('rois') or []
            ),
        }


def _local_autotrack_snapshot() -> dict:
    with _LOCAL_AUTOTRACK_LOCK:
        return dict(_LOCAL_AUTOTRACK)


def _write_local_vision_config_locked() -> None:
    path = _omnitrack_root() / 'vision_4000.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(_LOCAL_VISION_SETTINGS, separators=(',', ':')), encoding='utf-8')
    os.replace(temp, path)


def _save_local_vision_config(vision_cfg: dict) -> None:
    global _LOCAL_VISION_SETTINGS
    mode = str(vision_cfg.get('mode') or 'off')
    camera_modes = vision_cfg.get('camera_modes') or {}
    rois = vision_cfg.get('rois', (_LOCAL_VISION_SETTINGS or {}).get('rois', []))
    if mode not in {'off', 'advisory', 'active'} or not isinstance(camera_modes, dict):
        raise ValueError('Invalid local vision mode configuration')
    if not isinstance(rois, list) or any(not isinstance(roi, dict) for roi in rois):
        raise ValueError('Invalid local vision ROIs')
    with _LOCAL_VISION_SETTINGS_LOCK:
        _LOCAL_VISION_SETTINGS = {
            'mode': mode,
            'camera_modes': {
                str(camera): str(value)
                for camera, value in camera_modes.items()
                if str(value) in {'off', 'advisory', 'active'}
            },
            'rois': [dict(roi) for roi in rois],
        }
        _write_local_vision_config_locked()
    _invalidate_vision_cache()


def _mic_activity_hint() -> bool | None:
    """Return True/False if a mic is actively connected, or None to rely purely on visual lip-sync."""
    try:
        from services.port4000_mic_service import get_port4000_mic_service
        svc = get_port4000_mic_service()
        rt = getattr(svc, '_runtime', None)
        if rt is None:
            return None
        mics = [rt.mic, *list(getattr(rt, 'microphones', {}).values())]
        connected = [m for m in mics if m is not None and m.status().get('state') == 'Connected']
        if not connected:
            return None
        for m in connected:
            pos = m.status().get('position') or {}
            act = pos.get('activity')
            lvl = pos.get('level')
            if act is True or (isinstance(lvl, (int, float)) and lvl > -58.0):
                return True
        return False
    except Exception:
        return None


def _process_local_vision(
    cid: str,
    preset: str,
    pixels_b64: str,
    synced: dict,
    quiet: bool,
    hi_res_bgr=None,
) -> dict:
    source_root = Path(os.environ.get('CONFDECK_SOURCE_ROOT', '/home/ordain/sehnizer')).resolve()
    if not (source_root / 'confdeck' / 'vision' / 'mouth.py').is_file():
        raise RuntimeError(f'ConfDeck vision engine not found under {source_root}')
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

    import numpy as np
    from confdeck.vision.mouth import SeatVisionDetector

    raw = base64.b64decode(pixels_b64, validate=True)
    if len(raw) != 320 * 180:
        raise ValueError('Expected 320x180 grayscale bytes')
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(180, 320)

    keys = _camera_keys(cid, synced)
    config = _local_vision_config(synced)
    rois = {}
    for roi in config.get('rois') or []:
        if not isinstance(roi, dict) or roi.get('camera') not in keys:
            continue
        if roi.get('preset') and preset and roi['preset'] != preset:
            continue
        normalized = dict(roi)
        normalized['camera'] = cid
        normalized['preset'] = str(roi.get('preset') or preset or '')
        normalized['seat'] = str(roi.get('seat') or '').strip() or 'Seat'
        normalized['rect'] = _clamp_box(roi.get('rect') or [0, 0, 1, 1])
        normalized['masks'] = [
            _clamp_box(mask)
            for mask in (roi.get('masks') or [])[:8]
            if isinstance(mask, (list, tuple)) and len(mask) == 4
        ]
        rois[normalized['seat']] = normalized

    with _LOCAL_ENGINE_LOCK:
        engine = _LOCAL_ENGINES.get(cid)
        if engine is None:
            engine = SeatVisionDetector()
            _LOCAL_ENGINES[cid] = engine
        engine_lock = _LOCAL_ENGINE_LOCKS.setdefault(cid, threading.Lock())

    mic_hint = _mic_activity_hint()
    with engine_lock:
        if engine.detector is None:
            raise RuntimeError(engine.warning or 'YuNet face detector failed to initialize')
        result = engine.evaluate(
            pixels,
            rois,
            time.monotonic(),
            quiet=quiet,
            hi_res_frame=hi_res_bgr,
            mic_active=mic_hint,
        )
        if engine.disabled:
            raise RuntimeError(engine.warning or 'Local vision detector disabled itself')
        return {
            **result,
            'mode': _effective_camera_mode(cid, config, keys),
            'tracking': {
                'active': False,
                'pose': '',
                'seat': '',
                'reason': 'local_vision',
                'target_box': None,
                'post_preset': False,
            },
            'warning': engine.warning,
            'detector_backend': engine.backend_name,
            'detector_backend_requested': engine.backend_requested,
        }


def _local_tracking_command(
    center_x: float,
    center_y: float,
    previous_direction: str = '',
    is_moving: bool = False,
) -> tuple[str, int, int, int, float]:
    """Compute smooth axis-independent VISCA pan/tilt speeds with Schmitt-trigger deadband."""
    offset_x = center_x - 160.0
    offset_y = center_y - 90.0
    previous_parts = set(previous_direction.split('_')) if previous_direction and previous_direction != 'stop' else set()

    # Stop slightly early when already moving to absorb RTSP latency (~150-200ms) without overshoot.
    x_deadband = 15.0 if is_moving else 24.0
    y_deadband = 11.0 if is_moving else 17.0
    if ('right' in previous_parts and offset_x < 0) or ('left' in previous_parts and offset_x > 0):
        x_deadband = 32.0
    if ('down' in previous_parts and offset_y < 0) or ('up' in previous_parts and offset_y > 0):
        y_deadband = 22.0

    abs_x = abs(offset_x)
    abs_y = abs(offset_y)

    parts = []
    tilt_speed = 0
    if abs_y > y_deadband:
        parts.append('up' if offset_y < 0 else 'down')
        if abs_y < 32.0:
            tilt_speed = 1
        elif abs_y < 56.0:
            tilt_speed = 2
        else:
            tilt_speed = 3

    pan_speed = 0
    if abs_x > x_deadband:
        parts.append('left' if offset_x < 0 else 'right')
        if abs_x < 40.0:
            pan_speed = 1
        elif abs_x < 68.0:
            pan_speed = 2
        elif abs_x < 100.0:
            pan_speed = 4
        else:
            pan_speed = 6

    direction = '_'.join(parts) or 'stop'
    severity = max(abs_x / 160.0, abs_y / 90.0)
    if direction == 'stop':
        speed = 0
    else:
        speed = max(1, min(3, round(max(pan_speed / 2.4, tilt_speed / 2.0))))
    return direction, speed, pan_speed, tilt_speed, severity


def _local_tracking_target(all_faces) -> tuple[list[int] | None, str, int]:
    boxes = []
    for raw in all_faces or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            continue
        try:
            box = _clamp_box(raw[:4])
        except (TypeError, ValueError, OverflowError):
            continue
        boxes.append(box)

    if not boxes:
        return None, 'stop', 0

    box = max(boxes, key=lambda item: item[2] * item[3])
    center_x = box[0] + box[2] / 2
    center_y = box[1] + box[3] / 2
    direction, speed, _, _, _ = _local_tracking_command(center_x, center_y)
    return box, direction, speed


def _local_directions_are_opposite(direction: str, previous: str) -> bool:
    opposites = {'left': 'right', 'right': 'left', 'up': 'down', 'down': 'up'}
    direction_parts = set(direction.split('_'))
    previous_parts = set(previous.split('_'))
    return any(part in direction_parts and opposite in previous_parts for part, opposite in opposites.items())


def _schedule_local_tracking_stop(cid: str, command_at: float, delay: float) -> None:
    previous = _LOCAL_TRACKING_STOP_TIMERS.get(cid)
    if previous:
        previous[1].cancel()
    timer = threading.Timer(delay, _stop_local_tracking_pulse, args=(cid, command_at))
    timer.daemon = True
    _LOCAL_TRACKING_STOP_TIMERS[cid] = (command_at, timer)
    timer.start()


def _cancel_local_tracking_stop(cid: str) -> None:
    with _LOCAL_AUTOTRACK_LOCK:
        scheduled = _LOCAL_TRACKING_STOP_TIMERS.pop(cid, None)
        if scheduled:
            scheduled[1].cancel()


def _stop_local_tracking_pulse(cid: str, command_at: float) -> None:
    with _LOCAL_AUTOTRACK_LOCK:
        scheduled = _LOCAL_TRACKING_STOP_TIMERS.get(cid)
        runtime = _LOCAL_TRACKING_RUNTIME.get(cid)
        if not scheduled or scheduled[0] != command_at:
            return
        if not runtime or runtime.get('pulse_at') != command_at:
            _LOCAL_TRACKING_STOP_TIMERS.pop(cid, None)
            return
        _LOCAL_TRACKING_STOP_TIMERS.pop(cid, None)
        try:
            result = stop_camera(cid)
            if not result.get('success'):
                raise RuntimeError(str(result.get('error') or 'Camera did not accept the PTZ stop'))
        except Exception as exc:
            warning = f'Local PTZ tracking stop failed: {exc}'
            runtime['warning'] = warning
            print(f'[VISION] Camera {cid} local PTZ tracking stop failed: {exc}', flush=True)
            retry_at = time.monotonic()
            _MOVING_UNTIL[cid] = max(
                _MOVING_UNTIL.get(cid, 0.0),
                retry_at + 0.25 + _LOCAL_TRACKING_SETTLE_SECONDS,
            )
            _schedule_local_tracking_stop(cid, command_at, 0.25)
            return

        stopped_at = time.monotonic()
        runtime.update(
            direction='stop',
            speed=0,
            pan_speed=0,
            tilt_speed=0,
            last_command_at=stopped_at,
            stop_pending=False,
            retry_after=0.0,
        )
        _MOVING_UNTIL[cid] = max(
            _MOVING_UNTIL.get(cid, 0.0),
            stopped_at + _LOCAL_TRACKING_SETTLE_SECONDS,
        )


_LOCAL_ZOOM_TIMERS = {}
_LOCAL_ZOOM_ACTIVE = {}


def _cancel_local_zoom(cid: str, send_stop: bool = False) -> None:
    timer = _LOCAL_ZOOM_TIMERS.pop(cid, None)
    if timer:
        timer.cancel()
    was_active = _LOCAL_ZOOM_ACTIVE.pop(cid, False)
    if send_stop and was_active:
        try:
            zoom_camera(cid, 'stop')
        except Exception:
            pass


def _reset_local_group_lock(cid: str) -> None:
    """Reset super-seat speaker lock and bounded zoom budget when a new preset is recalled."""
    _cancel_local_zoom(cid, send_stop=True)
    with _LOCAL_ENGINE_LOCK:
        engine = _LOCAL_ENGINES.get(cid)
    if engine is not None and getattr(engine, 'group_selector', None) is not None:
        engine.group_selector.reset()


def _apply_local_zoom(cid: str, zoom_action: str) -> None:
    """Run bounded smooth zoom-in pulses with automatic stop timer to frame locked speaker."""
    now = time.monotonic()
    if zoom_action == 'stop':
        _cancel_local_zoom(cid, send_stop=True)
        return
    if zoom_action != 'in':
        return
    if _LOCAL_ZOOM_ACTIVE.get(cid):
        return
    with _LOCAL_ENGINE_LOCK:
        engine = _LOCAL_ENGINES.get(cid)
    selector = getattr(engine, 'group_selector', None) if engine is not None else None
    if selector is not None and (now - selector.last_zoom_at) < 0.45:
        return
    pulse_s = 0.28
    try:
        res = zoom_camera(cid, 'in', speed=2)
        if res.get('success'):
            _LOCAL_ZOOM_ACTIVE[cid] = True
            if selector is not None:
                selector.record_zoom_pulse(pulse_s, now)
            timer = threading.Timer(pulse_s, lambda: _cancel_local_zoom(cid, send_stop=True))
            timer.daemon = True
            _LOCAL_ZOOM_TIMERS[cid] = timer
            timer.start()
    except Exception:
        pass


def _update_local_autotrack(
    cid: str,
    all_faces,
    quiet: bool = False,
    group_speaker: dict | None = None,
) -> dict:
    # Prefer verified lip-sync active speaker inside multi-person group over blind largest-face
    group_box = (group_speaker or {}).get('target_box')
    group_reason = (group_speaker or {}).get('reason') or ''
    zoom_action = (group_speaker or {}).get('zoom_action') or 'none'
    if group_box is not None:
        box = _clamp_box(group_box)
    elif group_reason == 'waiting_for_speaker' and len(all_faces or []) > 1:
        box = None
    else:
        box, _, _ = _local_tracking_target(all_faces)
    now = time.monotonic()
    warning = ''
    with _LOCAL_AUTOTRACK_LOCK:
        autotrack = dict(_LOCAL_AUTOTRACK)
        if not autotrack['enabled'] or autotrack['camera'] != cid:
            return {
                'active': False,
                'pose': '',
                'seat': '',
                'reason': 'stopped',
                'target_box': None,
                'post_preset': False,
            }

        runtime = _LOCAL_TRACKING_RUNTIME.setdefault(cid, {
            'direction': 'stop',
            'speed': 0,
            'pan_speed': 0,
            'tilt_speed': 0,
            'last_command_at': 0.0,
            'last_direction': '',
            'filtered_center': None,
            'pending_direction': '',
            'pending_count': 0,
            'retry_after': 0.0,
            'stop_pending': False,
            'pulse_at': 0.0,
            'last_box': None,
            'last_face_at': 0.0,
        })
        runtime.setdefault('pan_speed', 0)
        runtime.setdefault('tilt_speed', 0)
        runtime.setdefault('last_direction', '')
        runtime.setdefault('filtered_center', None)
        runtime.setdefault('pending_direction', '')
        runtime.setdefault('pending_count', 0)
        runtime.setdefault('retry_after', 0.0)
        runtime.setdefault('stop_pending', False)
        runtime.setdefault('pulse_at', 0.0)
        runtime.setdefault('last_box', None)
        runtime.setdefault('last_face_at', 0.0)
        warning = runtime.pop('warning', '')

        if quiet or runtime['stop_pending'] or now < _MOVING_UNTIL.get(cid, 0.0):
            tracking = {
                'active': not bool(warning),
                'pose': 'front' if runtime['last_box'] else '',
                'seat': '',
                'reason': 'camera_settling' if runtime['last_box'] else 'waiting_for_face',
                'target_box': runtime['last_box'],
                'post_preset': False,
                'direction': runtime['direction'],
            }
            if warning:
                tracking['warning'] = warning
            return tracking

        is_moving = runtime['direction'] != 'stop'
        if box:
            runtime['last_face_at'] = now
            runtime['last_box'] = box
            measured_x = box[0] + box[2] / 2
            measured_y = box[1] + box[3] / 2
            previous_center = runtime['filtered_center']
            if previous_center:
                alpha = _LOCAL_TRACKING_SMOOTHING_ALPHA
                center_x = alpha * measured_x + (1 - alpha) * previous_center[0]
                center_y = alpha * measured_y + (1 - alpha) * previous_center[1]
            else:
                center_x, center_y = measured_x, measured_y
            runtime['filtered_center'] = (center_x, center_y)
            direction, speed, pan_speed, tilt_speed, _ = _local_tracking_command(
                center_x,
                center_y,
                runtime['last_direction'],
                is_moving=is_moving,
            )
        else:
            # Brief grace period so 1-frame detection dropouts don't cause jerky stops.
            if is_moving and (now - runtime['last_face_at']) < _LOCAL_TRACKING_LOST_GRACE_SECONDS:
                return {
                    'active': not bool(warning),
                    'pose': 'front' if runtime['last_box'] else '',
                    'seat': '',
                    'reason': 'local_vision',
                    'target_box': runtime['last_box'],
                    'post_preset': False,
                    'direction': runtime['direction'],
                }
            direction, speed, pan_speed, tilt_speed = 'stop', 0, 0, 0
            if (now - runtime['last_face_at']) >= _LOCAL_TRACKING_LOST_GRACE_SECONDS:
                runtime['filtered_center'] = None
                runtime['last_box'] = None

        reversal_stop = False
        if direction != 'stop' and _local_directions_are_opposite(direction, runtime['last_direction']):
            if is_moving:
                # Stop immediately and settle briefly before reversing to prevent oscillation.
                direction, speed, pan_speed, tilt_speed = 'stop', 0, 0, 0
                reversal_stop = True
            else:
                if direction == runtime['pending_direction']:
                    runtime['pending_count'] += 1
                else:
                    runtime.update(pending_direction=direction, pending_count=1)
                if runtime['pending_count'] < _LOCAL_TRACKING_REVERSE_CONFIRMATIONS:
                    direction, speed, pan_speed, tilt_speed = 'stop', 0, 0, 0
        else:
            runtime.update(pending_direction='', pending_count=0)

        velocity_changed = (
            direction != runtime['direction']
            or pan_speed != runtime['pan_speed']
            or tilt_speed != runtime['tilt_speed']
        )
        lease_refresh_due = (now - runtime['last_command_at']) >= _LOCAL_TRACKING_REFRESH_SECONDS
        should_send_move = (
            direction != 'stop'
            and now >= runtime['retry_after']
            and (velocity_changed or lease_refresh_due)
        )
        should_send_stop = (
            direction == 'stop'
            and is_moving
            and now >= runtime['retry_after']
        )
        try:
            if should_send_move:
                result = move_camera(
                    cid,
                    direction,
                    speed=speed,
                    duration=_LOCAL_TRACKING_LEASE_SECONDS,
                    pan_speed=pan_speed,
                    tilt_speed=tilt_speed,
                )
                if not result.get('success'):
                    warning = str(result.get('error') or 'Camera did not accept the PTZ move')
                    runtime.update(
                        last_command_at=now,
                        retry_after=now + 0.25,
                    )
                else:
                    command_at = time.monotonic()
                    runtime.update(
                        direction=direction,
                        speed=speed,
                        pan_speed=pan_speed,
                        tilt_speed=tilt_speed,
                        last_direction=direction,
                        last_command_at=command_at,
                        pending_direction='',
                        pending_count=0,
                        retry_after=0.0,
                        stop_pending=False,
                        pulse_at=command_at,
                    )
                    # Deadman watchdog stop in case frames stop arriving; refreshed smoothly on next move.
                    _schedule_local_tracking_stop(cid, command_at, _LOCAL_TRACKING_LEASE_SECONDS + 0.10)
            elif should_send_stop:
                _cancel_local_tracking_stop(cid)
                result = stop_camera(cid)
                if not result.get('success'):
                    warning = str(result.get('error') or 'Camera did not accept the PTZ stop')
                    runtime['retry_after'] = now + 0.25
                else:
                    stopped_at = time.monotonic()
                    runtime.update(
                        direction='stop',
                        speed=0,
                        pan_speed=0,
                        tilt_speed=0,
                        last_command_at=stopped_at,
                        stop_pending=False,
                        retry_after=0.0,
                    )
                    if reversal_stop:
                        runtime.update(last_direction='', pending_direction='', pending_count=0)
                        _MOVING_UNTIL[cid] = max(
                            _MOVING_UNTIL.get(cid, 0.0),
                            stopped_at + _LOCAL_TRACKING_SETTLE_SECONDS,
                        )
        except Exception as exc:
            warning = f'Local PTZ tracking failed: {exc}'
            print(f'[VISION] Camera {cid} local PTZ tracking failed: {exc}', flush=True)

        if not quiet and zoom_action in ('in', 'stop'):
            _apply_local_zoom(cid, zoom_action)

        tracking = {
            'active': not bool(warning),
            'pose': 'front' if box else '',
            'seat': '',
            'reason': group_reason or ('local_vision' if box else 'waiting_for_face'),
            'target_box': box,
            'locked_track_id': (group_speaker or {}).get('locked_track_id'),
            'zoom_action': zoom_action,
            'post_preset': False,
            'direction': runtime['direction'],
        }
        if warning:
            tracking['warning'] = warning
        return tracking


def _active_vision_forward_loop(cid: str) -> None:
    if _PORT4000_VISION_LOCAL_ONLY:
        return
    while True:
        with _ACTIVE_FORWARD_LOCK:
            payload = _ACTIVE_FORWARD_PENDING.pop(cid, None)
            if payload is None:
                _ACTIVE_FORWARD_RUNNING.discard(cid)
                return
        try:
            data, code = _call_confdeck('accuracy/vision/frame', body=payload, method='POST', timeout=4)
            if code >= 400:
                print(f'[VISION] Port 8765 active score feed failed for {cid}: {data}', flush=True)
        except Exception as exc:
            print(f'[VISION] Port 8765 active score feed failed for {cid}: {exc}', flush=True)


def _enqueue_active_vision_frame(cid: str, payload: dict) -> None:
    if _PORT4000_VISION_LOCAL_ONLY:
        return
    with _ACTIVE_FORWARD_LOCK:
        _ACTIVE_FORWARD_PENDING[cid] = payload
        if cid in _ACTIVE_FORWARD_RUNNING:
            return
        _ACTIVE_FORWARD_RUNNING.add(cid)
    try:
        threading.Thread(
            target=_active_vision_forward_loop,
            args=(cid,),
            name=f'omni4000-active-vision-{cid}',
            daemon=True,
        ).start()
    except Exception:
        with _ACTIVE_FORWARD_LOCK:
            _ACTIVE_FORWARD_PENDING.pop(cid, None)
            _ACTIVE_FORWARD_RUNNING.discard(cid)
        raise


def _submit_vision_frame(
    cid: str,
    preset: str,
    pixels_b64: str,
    source: str = 'client',
    alias: str = '',
    hi_res_bgr=None,
) -> tuple[dict, int]:
    synced = _sync_cameras_with_confdeck(force=False)
    is_quiet = time.monotonic() < _MOVING_UNTIL.get(cid, 0.0)
    if source != 'rtsp':
        _LAST_CLIENT_FRAME[cid] = time.monotonic()

    vision_cfg = _local_vision_config(synced)
    keys = _camera_keys(cid, synced)
    autotrack = synced.get('autotrack') or {}
    auto_camera = str(autotrack.get('camera') or '').strip()
    auto_cid = synced['alias_to_id'].get(auto_camera, auto_camera) if autotrack.get('enabled') else ''
    is_auto = bool(auto_cid and cid == auto_cid and autotrack.get('mode') == 'vision')
    effective_mode = _effective_camera_mode(cid, vision_cfg, keys)
    is_post_preset = time.monotonic() < _POST_PRESET_UNTIL.get(cid, 0.0)
    if effective_mode == 'off' and not is_auto:
        return {'mode': 'off', 'scores': {}}, 200

    try:
        out = _process_local_vision(cid, preset, pixels_b64, synced, is_quiet, hi_res_bgr=hi_res_bgr)
    except Exception as exc:
        message = f'Port 4000 local vision unavailable: {exc}'
        print(f'[VISION] {message}', flush=True)
        tracking = (
            _update_local_autotrack(cid, [], quiet=is_quiet)
            if is_auto and _PORT4000_VISION_LOCAL_ONLY else None
        )
        if (effective_mode == 'active' or is_post_preset) and cid in synced['cd_ids'] and not is_auto:
            _enqueue_active_vision_frame(cid, {
                'camera': cid,
                'preset': preset,
                'pixels': pixels_b64,
                'quiet': is_quiet,
            })
        with _VISION_LOCK:
            _VISION_STATES[cid] = {
                'camera': cid,
                'alias': alias or cid,
                'preset': preset,
                'source': source,
                'frame_size': [320, 180],
                'updated_at': time.monotonic(),
                'wall_time': time.time(),
                'warning': message,
                'error': message,
                **({'tracking': tracking} if tracking is not None else {}),
            }
        return {'error': message}, 503

    if is_auto and _PORT4000_VISION_LOCAL_ONLY:
        out['tracking'] = _update_local_autotrack(
            cid,
            out.get('all_faces', []),
            quiet=is_quiet,
            group_speaker=out.get('group_speaker'),
        )
        if out['tracking'].get('warning'):
            out['warning'] = out['tracking']['warning']
    # Keep feeding frames to the existing 8765 camera-command owner only when
    # explicit vision autotrack is enabled; local detections remain authoritative.
    elif is_auto and cid in synced['cd_ids']:
        track, code = _call_confdeck(
            'accuracy/vision/frame',
            body={'camera': cid, 'preset': preset, 'pixels': pixels_b64, 'quiet': is_quiet},
            method='POST',
            timeout=4,
        )
        if code < 400 and isinstance(track, dict):
            out['tracking'] = track.get('tracking', out['tracking'])
        else:
            out['warning'] = 'Local vision is running; camera-tracking state is temporarily unavailable'
    elif (effective_mode == 'active' or is_post_preset) and cid in synced['cd_ids']:
        _enqueue_active_vision_frame(cid, {
            'camera': cid,
            'preset': preset,
            'pixels': pixels_b64,
            'quiet': is_quiet,
        })

    with _VISION_LOCK:
        _VISION_STATES[cid] = {
            **out,
            'camera': cid,
            'alias': alias or cid,
            'preset': preset,
            'source': source,
            'frame_size': [320, 180],
            'updated_at': time.monotonic(),
            'wall_time': time.time(),
        }
    return out, 200


def _ensure_port4000_worker():
    global _WORKER_STARTED
    if (
        'pytest' in sys.modules
        or os.environ.get('PYTEST_CURRENT_TEST')
        or os.environ.get('PORT4000_DISABLE_VISION_WORKER') == '1'
    ):
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
        threading.Thread(target=_port4000_vision_worker_loop, name='omni4000-vision', daemon=True).start()


class _LatestRTSPReader:
    """Continuously drains RTSP/FFmpeg buffers so vision always gets the freshest frame."""

    def __init__(self, url: str, cv2_mod):
        self.url = url
        self.cv2 = cv2_mod
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame = None
        self._seq = 0
        self._failed = False
        self._thread = threading.Thread(target=self._run, name='omni-rtsp-reader', daemon=True)
        self._thread.start()

    def _run(self):
        cap = self.cv2.VideoCapture(self.url, self.cv2.CAP_FFMPEG)
        try:
            cap.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if not cap.isOpened():
            cap.release()
            with self._lock:
                self._failed = True
            return
        try:
            while not self._stop.is_set():
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    with self._lock:
                        self._failed = True
                    break
                with self._lock:
                    self._frame = bgr
                    self._seq += 1
        finally:
            try:
                cap.release()
            except Exception:
                pass

    def latest(self, last_seq: int):
        with self._lock:
            if self._failed:
                return False, None, last_seq
            if self._frame is None or self._seq == last_seq:
                return True, None, last_seq
            return True, self._frame, self._seq

    def close(self):
        self._stop.set()


def _port4000_vision_worker_loop():
    try:
        import cv2
    except Exception:
        return

    os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'rtsp_transport;tcp|stimeout;1500000')
    readers: dict[str, _LatestRTSPReader] = {}
    last_seq: dict[str, int] = {}
    backoff_until = {}
    url_idx = {}
    last_push = {}

    while True:
        try:
            now = time.monotonic()
            synced = _sync_cameras_with_confdeck(force=False)
            vision_cfg = _local_vision_config(synced)
            autotrack = synced['autotrack']
            auto_cid = _resolve_camera_alias(autotrack.get('camera')) if autotrack.get('enabled') else ''
            omni_cams = {c['key']: c for c in _load_omnitrack_cameras()}
            active_targets = set()
            did_work = False

            # Track every ConfDeck camera plus any OmniTrack camera with a known URL
            target_cids = set(synced['cd_ids']) | {synced['alias_to_id'].get(k, k) for k in omni_cams}
            for cid in sorted(target_cids):
                keys = _camera_keys(cid, synced)
                eff_mode = _effective_camera_mode(cid, vision_cfg, keys)
                is_auto = bool(auto_cid and cid == auto_cid and autotrack.get('mode') == 'vision')
                if eff_mode == 'off' and not is_auto:
                    continue
                if now - _LAST_CLIENT_FRAME.get(cid, 0.0) < 1.6:
                    continue
                if now < _MOVING_UNTIL.get(cid, 0.0):
                    active_targets.add(cid)
                    continue

                min_interval = 0.04 if is_auto or eff_mode == 'active' else 0.12
                if now - last_push.get(cid, 0.0) < min_interval:
                    active_targets.add(cid)
                    continue
                if now < backoff_until.get(cid, 0.0):
                    continue

                oc = next((c for k, c in omni_cams.items() if k in keys), {})
                cd = next((c for c in synced['cd_cameras'] if isinstance(c, dict) and c.get('id') == cid), {})
                cd_ip = cd.get('ip') or oc.get('ip') or ''
                cd_port = cd.get('port') or 554
                cd_path = cd.get('path') or '/rtpstream/config1'
                candidates = []
                for u in (
                    f'rtsp://127.0.0.1:8554/{cid}' if cid in synced['cd_ids'] else '',
                    oc.get('rtsp_url', ''),
                    f'rtsp://{cd_ip}:{cd_port}{cd_path}' if cd_ip else '',
                    f"http://127.0.0.1:4000/api/ptz/preview_feed?camera={oc['key']}" if oc.get('key') else '',
                ):
                    if u and u not in candidates:
                        candidates.append(u)
                if not candidates:
                    continue

                active_targets.add(cid)
                idx = url_idx.get(cid, 0) % len(candidates)
                stream_url = candidates[idx]
                reader = readers.get(cid)
                if reader is None or reader.url != stream_url:
                    if reader is not None:
                        reader.close()
                    reader = _LatestRTSPReader(stream_url, cv2)
                    readers[cid] = reader
                    last_seq[cid] = 0

                ok, bgr, seq = reader.latest(last_seq.get(cid, 0))
                if not ok:
                    reader.close()
                    readers.pop(cid, None)
                    last_seq.pop(cid, None)
                    url_idx[cid] = (idx + 1) % len(candidates)
                    backoff_until[cid] = now + (0.3 if idx + 1 < len(candidates) else 2.0)
                    continue
                if bgr is None:
                    continue
                last_seq[cid] = seq

                small = cv2.resize(bgr, (320, 180), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                b64 = base64.b64encode(gray.tobytes()).decode('ascii')
                _submit_vision_frame(cid, '', b64, source='rtsp', alias=oc.get('key', cid), hi_res_bgr=bgr)
                last_push[cid] = time.monotonic()
                did_work = True

            for cid in list(readers.keys()):
                if cid not in active_targets:
                    try:
                        readers[cid].close()
                    except Exception:
                        pass
                    readers.pop(cid, None)
                    last_seq.pop(cid, None)

            time.sleep(0.015 if did_work else 0.1)
        except Exception:
            time.sleep(0.5)


def _invalidate_vision_cache():
    with _VISION_LOCK:
        _VISION_CACHE['expires'] = 0.0


def _save_tracking_vision(vision_cfg: dict) -> tuple[dict, int]:
    if _PORT4000_VISION_LOCAL_ONLY:
        return {'error': 'Tracking settings are stored locally in Port 4000'}, 501
    st_data, st_code = _call_confdeck('state', method='GET', timeout=3)
    if st_code >= 400 or not isinstance(st_data, dict):
        return (st_data if isinstance(st_data, dict) else {'error': 'ConfDeck state unavailable'}), st_code
    tracking = dict((st_data.get('settings') or {}).get('tracking') or {})
    tracking['vision'] = vision_cfg
    out, code = _call_confdeck('tracking/settings', body=tracking, method='PUT', timeout=5)
    _invalidate_vision_cache()
    return out, code


def _save_vision_mode(vision_cfg: dict, cd_body: dict | None = None, use_direct: bool = True) -> tuple[dict, int]:
    if _PORT4000_VISION_LOCAL_ONLY:
        _save_local_vision_config(vision_cfg)
        return {'ok': True, 'mode': vision_cfg.get('mode'), 'camera_modes': vision_cfg.get('camera_modes', {})}, 200
    if use_direct and cd_body:
        out, code = _call_confdeck('accuracy/vision/mode', body=cd_body, method='POST', timeout=5)
        if code not in (404, 405):
            _invalidate_vision_cache()
            return out, code
    return _save_tracking_vision(vision_cfg)


def _save_vision_rois(
    vision_cfg: dict,
    action: str,
    cid: str = '',
    preset: str = '',
    seat: str = '',
    roi: dict | None = None,
    rois: list | None = None,
    synced: dict | None = None,
) -> tuple[dict, int]:
    if _PORT4000_VISION_LOCAL_ONLY:
        _save_local_vision_config(vision_cfg)
        return {'ok': True, 'rois': vision_cfg.get('rois', [])}, 200

    cd_ids = (synced or {}).get('cd_ids') or set()
    use_direct = bool((action == 'remove' and not cid) or (cid and cid in cd_ids))
    if use_direct:
        payload: dict = {'action': action, 'preset': preset}
        if cid:
            payload['camera'] = cid
        if action == 'remove':
            payload['seat'] = seat
        elif action == 'add':
            payload['roi'] = roi or {}
        elif action == 'set_camera':
            payload['rois'] = rois or []
        out, code = _call_confdeck('accuracy/vision/rois', body=payload, method='POST', timeout=5)
        if code not in (404, 405):
            _invalidate_vision_cache()
            return out, code
    return _save_tracking_vision(vision_cfg)


@sennheiser_bp.after_app_request
def _bridge_ptz_and_camera_events(response):
    try:
        path = request.path or ''
        if response.status_code < 400 and request.method in ('POST', 'PUT', 'DELETE'):
            if path.startswith('/api/ptz/') and any(k in path for k in ('move', 'recall', 'preset', 'home', 'stop')):
                data = request.get_json(silent=True) or {}
                cam = data.get('camera') or data.get('camera_id') or request.args.get('camera')
                if cam:
                    cid = _resolve_camera_alias(str(cam))
                    _MOVING_UNTIL[cid] = max(_MOVING_UNTIL.get(cid, 0.0), time.monotonic() + 1.5)
                    if 'recall' in path or 'home' in path:
                        _reset_local_group_lock(cid)
                        preset_val = str(data.get('preset') or ('1' if 'home' in path else ''))
                        seat_val = data.get('seat')
                        if not seat_val and preset_val:
                            cam_num = str(cam).split('_')[-1]
                            for s in _load_omnitrack_seats():
                                if str(s.get('camera', '')) in (str(cam), cam_num) and str(s.get('preset', '')) == preset_val:
                                    seat_val = s.get('seat_id') or s.get('id')
                                    break
                        payload = {
                            'camera': cid,
                            'preset': preset_val,
                            'seconds': 1.5,
                            'track': True,
                        }
                        if seat_val:
                            payload['seat'] = seat_val
                        _POST_PRESET_UNTIL[cid] = max(
                            _POST_PRESET_UNTIL.get(cid, 0.0),
                            time.monotonic() + 3.0,
                        )
                        if not _PORT4000_VISION_LOCAL_ONLY:
                            threading.Thread(
                                target=lambda: _call_confdeck('accuracy/vision/preset-recalled', body=payload, method='POST', timeout=1.5),
                                daemon=True,
                            ).start()
            elif path.startswith('/api/camera') or 'camera_config' in path:
                threading.Thread(target=lambda: _sync_cameras_with_confdeck(force=True), daemon=True).start()
    except Exception:
        pass
    return response


@sennheiser_bp.route('/api/sennheiser/vision/cameras', methods=['GET'])
def sennheiser_vision_cameras():
    _ensure_port4000_worker()
    synced = _sync_cameras_with_confdeck(force=True)
    omni_cams = {c['key']: c for c in _load_omnitrack_cameras()}
    cd_cams = synced['cd_cameras']
    vision_cfg = _local_vision_config(synced)
    autotrack = synced['autotrack']
    auto_camera = str(autotrack.get('camera') or '').strip()
    auto_cid = synced['alias_to_id'].get(auto_camera, auto_camera) if autotrack.get('enabled') else ''
    all_rois = list(vision_cfg.get('rois') or [])

    by_ip = {c.get('ip'): c for c in cd_cams if isinstance(c, dict) and c.get('ip')}
    by_id = {c.get('id'): c for c in cd_cams if isinstance(c, dict) and c.get('id')}

    host = (request.host or '127.0.0.1').split(':')[0]
    merged = []
    seen_ids = set()

    def _cam_rois(keys: set[str]):
        return [r for r in all_rois if isinstance(r, dict) and r.get('camera') in keys]

    for key, oc in omni_cams.items():
        cd = by_id.get(key) or by_ip.get(oc.get('ip')) or {}
        cid = cd.get('id') or key
        seen_ids.add(cid)
        keys = _camera_keys(cid, synced) | {key}
        native = bool(cd.get('native_tracking', False))
        auto_mode = 'vision' if (auto_cid == cid and autotrack.get('mode') == 'vision') else ('native' if native else 'off')
        merged.append({
            'id': cid,
            'key': key,
            'name': oc.get('name') or cd.get('name') or key,
            'ip': oc.get('ip') or cd.get('ip') or '',
            'protocol': oc.get('protocol') or '',
            'brand': oc.get('brand') or '',
            'enabled': oc.get('enabled', True),
            'confdeck_registered': cid in synced['cd_ids'],
            'mode': _effective_camera_mode(cid, vision_cfg, keys),
            'autotrack_mode': auto_mode,
            'native_tracking': native,
            'rois': _cam_rois(keys),
            'local_rtsp': oc.get('local_rtsp') or '',
            'whep_url': f'http://{host}:8889/{cid}/whep',
            'hls_url': f'http://{host}:8888/{cid}/index.m3u8',
            'mjpeg_url': f'/api/ptz/preview_feed?camera={key}',
        })

    for cd in cd_cams:
        if not isinstance(cd, dict):
            continue
        cid = cd.get('id')
        if not cid or cid in seen_ids:
            continue
        keys = _camera_keys(cid, synced)
        native = bool(cd.get('native_tracking', False))
        auto_mode = 'vision' if (auto_cid == cid and autotrack.get('mode') == 'vision') else ('native' if native else 'off')
        merged.append({
            'id': cid,
            'key': cid,
            'name': cd.get('name') or cid,
            'ip': cd.get('ip') or '',
            'protocol': 'visca' if (cd.get('control_protocol') == 'visca' or cd.get('ip') in ('192.168.1.108', '192.168.1.109', '192.168.1.128')) else str(cd.get('control_protocol') or ''),
            'brand': '',
            'enabled': True,
            'confdeck_registered': True,
            'mode': _effective_camera_mode(cid, vision_cfg, keys),
            'autotrack_mode': auto_mode,
            'native_tracking': native,
            'rois': _cam_rois(keys),
            'local_rtsp': f'rtsp://127.0.0.1:8554/{cid}',
            'whep_url': f'/api/media/{cid}/whep',
            'hls_url': f'/api/media/{cid}/hls/index.m3u8',
            'mjpeg_url': f'/api/ptz/preview_feed?camera={cid}',
        })

    return jsonify({
        'mode': vision_cfg.get('mode', 'off'),
        'autotrack_camera': auto_cid,
        'cameras': merged,
        'seats': _load_omnitrack_seats(),
    })


def _handle_accuracy_resource(resource: str):
    if (request.content_length or 0) > 4_500_000:
        return jsonify(error='Payload too large'), 413

    body = request.get_json(silent=True) if request.method in ('POST', 'PUT', 'DELETE') else None
    body = body if isinstance(body, dict) else {}

    if resource == 'vision/cameras' and request.method == 'GET':
        return sennheiser_vision_cameras()

    if resource == 'vision/state' and request.method == 'GET':
        _ensure_port4000_worker()
        synced = _sync_cameras_with_confdeck(force=False)
        vision_cfg = _local_vision_config(synced)
        autotrack = synced['autotrack']
        auto_cid = _resolve_camera_alias(autotrack.get('camera')) if autotrack.get('enabled') else ''
        raw_cam = request.args.get('camera')
        if raw_cam:
            cid = _resolve_camera_alias(raw_cam)
            keys = _camera_keys(cid, synced)
            with _VISION_LOCK:
                st = dict(_VISION_STATES.get(cid, {}))
            stale = (not st) or ((time.monotonic() - float(st.get('updated_at') or 0.0)) > 0.8)
            if stale and cid in synced['cd_ids']:
                cd_st, cd_code = _call_confdeck(f"accuracy/vision/state?{urlencode({'camera': cid})}", method='GET', timeout=2)
                if cd_code < 400 and isinstance(cd_st, dict):
                    st['tracking'] = cd_st.get('tracking', st.get('tracking'))
                    st['autotrack'] = cd_st.get('autotrack', st.get('autotrack'))
            cam_obj = next((c for c in synced['cd_cameras'] if isinstance(c, dict) and c.get('id') == cid), {})
            native = bool(cam_obj.get('native_tracking', False))
            alias = next((k for k in sorted(keys) if k.startswith('camera_')), st.get('alias') or cid)
            auto_on = bool((auto_cid == cid and autotrack.get('enabled')) or native)
            cd_auto = st.get('autotrack') if isinstance(st.get('autotrack'), dict) else {}
            post_preset_active = bool(cd_auto.get('post_preset') or (st.get('tracking') or {}).get('post_preset'))
            track_payload = dict(st.get('tracking') or {})
            if not auto_on and not post_preset_active and track_payload.get('active'):
                track_payload = {'active': False, 'pose': '', 'seat': '', 'reason': 'stopped', 'target_box': None, 'post_preset': False}
            return jsonify({
                'scores': st.get('scores', {}),
                'motion': st.get('motion', {}),
                'occupancy': st.get('occupancy', {}),
                'speaking': st.get('speaking', {}),
                'faces': st.get('faces', {}),
                'all_faces': st.get('all_faces', []),
                'unassigned_faces': st.get('unassigned_faces', []),
                'poses': st.get('poses', {}),
                'binding_scores': st.get('binding_scores', {}),
                'binding_rejected': st.get('binding_rejected', {}),
                'teaching_targets': st.get('teaching_targets', {}),
                'tracking': track_payload,
                'warning': st.get('warning', ''),
                'wall_time': st.get('wall_time'),
                'source': st.get('source'),
                'frame_size': st.get('frame_size', [320, 180]),
                'camera': cid,
                'alias': alias,
                'mode': vision_cfg.get('mode', 'off'),
                'camera_mode': _effective_camera_mode(cid, vision_cfg, keys),
                'rois': [r for r in (vision_cfg.get('rois') or []) if isinstance(r, dict) and r.get('camera') in keys],
                'autotrack': {
                    'enabled': bool(auto_on or post_preset_active),
                    'mode': 'vision' if (auto_cid == cid and autotrack.get('mode') == 'vision') else ('native' if native else ('post_preset' if post_preset_active else 'off')),
                    'camera': cid,
                    'post_preset': post_preset_active,
                    'locked_seat': cd_auto.get('locked_seat', ''),
                },
            })
        return jsonify({
            'mode': vision_cfg.get('mode', 'off'),
            'autotrack_camera': auto_cid,
            'cameras': {k: _VISION_STATES.get(k, {}) for k in synced['cd_ids'] | set(_VISION_STATES.keys())},
            'rois': list(vision_cfg.get('rois') or []),
        })

    if resource == 'vision/mode' and request.method == 'POST':
        synced = _sync_cameras_with_confdeck(force=True)
        vision_cfg = _local_vision_config(synced)
        vision_cfg = {**vision_cfg, 'camera_modes': dict(vision_cfg['camera_modes'])}
        valid = {'off', 'advisory', 'active'}
        if body.get('mode') is not None:
            m = str(body['mode'])
            if m not in valid:
                return jsonify(error=f'Unsupported vision mode: {m}'), 400
            vision_cfg['mode'] = m
        if body.get('camera') is not None and body.get('camera_mode') is not None:
            cm = str(body['camera_mode'])
            if cm not in valid:
                return jsonify(error=f'Unsupported camera vision mode: {cm}'), 400
            cid = _resolve_camera_alias(body['camera'])
            if cm != 'off' and vision_cfg.get('mode', 'off') == 'off':
                vision_cfg['mode'] = cm
            vision_cfg['camera_modes'][cid] = cm
        _save_local_vision_config(vision_cfg)
        _ensure_port4000_worker()
        return jsonify({'ok': True, 'mode': vision_cfg['mode'], 'camera_modes': vision_cfg['camera_modes']})

    if resource == 'vision/rois' and request.method == 'POST':
        synced = _sync_cameras_with_confdeck(force=True)
        vision_cfg = dict(synced['vision_cfg'])
        existing = [dict(r) for r in (vision_cfg.get('rois') or []) if isinstance(r, dict)]
        action = str(body.get('action') or 'set_camera').strip()
        cid = _resolve_camera_alias(body.get('camera'))
        keys = _camera_keys(cid, synced) if cid else set()
        preset = str(body.get('preset') or '').strip()
        added_roi = None
        incoming = []
        seat = ''

        if action == 'clear_camera' and cid:
            existing = [
                r for r in existing
                if not (r.get('camera') in keys and (not preset or r.get('preset', '') in ('', preset)))
            ]
        elif action == 'remove':
            seat = str(body.get('seat') or '').strip()
            existing = [
                r for r in existing
                if not (r.get('seat') == seat and (not keys or r.get('camera') in keys))
            ]
        elif action == 'add':
            added_roi = _normalize_roi_dict(body.get('roi') or body, cid, preset)
            r_keys = _camera_keys(added_roi['camera'], synced)
            cid = added_roi['camera']
            keys = r_keys
            existing = [
                r for r in existing
                if not (r.get('seat') == added_roi['seat'] and r.get('camera') in r_keys and r.get('preset', '') == added_roi['preset'])
            ]
            existing.append(added_roi)
        elif action == 'set_camera' and cid:
            kept = [
                r for r in existing
                if not (r.get('camera') in keys and (not preset or r.get('preset', '') in ('', preset)))
            ]
            incoming = [_normalize_roi_dict(item, cid, preset) for item in (body.get('rois') or []) if isinstance(item, dict)]
            existing = kept + incoming
        else:
            return jsonify(error=f'Unsupported ROI action: {action}'), 400

        vision_cfg['rois'] = existing
        out, code = _save_vision_rois(
            vision_cfg,
            action=action,
            cid=cid,
            preset=preset,
            seat=seat,
            roi=added_roi,
            rois=incoming,
            synced=synced,
        )
        if code >= 400:
            return jsonify(out), code
        if isinstance(out, dict) and isinstance(out.get('rois'), list) and cid in synced['cd_ids']:
            return jsonify({'ok': True, 'rois': out['rois']})
        return jsonify({
            'ok': True,
            'rois': [r for r in existing if not keys or r.get('camera') in keys],
        })

    if resource in ('vision/track', 'vision/autotrack') and request.method == 'POST':
        if _PORT4000_VISION_LOCAL_ONLY:
            req_mode = str(body.get('mode') or ('off' if body.get('enabled') is False else 'vision')).strip()
            enabled = bool(body.get('enabled', req_mode != 'off')) and req_mode != 'off'
            synced = _sync_cameras_with_confdeck(force=True)
            cid = _resolve_camera_alias(body.get('camera'))
            if enabled and req_mode != 'vision':
                return jsonify(error='Native camera tracking is unavailable in local mode; choose Vision tracking.'), 409

            if not enabled:
                previous = _local_autotrack_snapshot()
                stop_cid = str(previous.get('camera') or cid or '')
                try:
                    if stop_cid:
                        result = stop_camera(stop_cid)
                        if not result.get('success'):
                            raise RuntimeError(str(result.get('error') or 'Camera did not accept the PTZ stop'))
                    with _LOCAL_AUTOTRACK_LOCK:
                        _LOCAL_AUTOTRACK.update(enabled=False, camera='', mode='off')
                        if stop_cid:
                            _cancel_local_tracking_stop(stop_cid)
                            _LOCAL_TRACKING_RUNTIME.pop(stop_cid, None)
                except Exception as exc:
                    return jsonify(error=f'Unable to stop local camera tracking: {exc}'), 502
                _invalidate_vision_cache()
                return jsonify({'ok': True, 'enabled': False, 'mode': 'off'})

            if not cid:
                return jsonify(error='A camera must be selected for Vision tracking.'), 400
            known_cameras = {str(camera.get('key') or '') for camera in _load_omnitrack_cameras()}
            if cid not in known_cameras:
                return jsonify(error=f'Unknown OmniTrack camera: {cid}'), 404
            if not get_camera_info(cid).get('ip'):
                return jsonify(error=f'Camera {cid} has no configured PTZ address.'), 409

            vision_cfg = _local_vision_config(synced)
            vision_cfg = {**vision_cfg, 'camera_modes': dict(vision_cfg['camera_modes'])}
            if vision_cfg.get('mode', 'off') == 'off':
                vision_cfg['mode'] = 'advisory'
            if _effective_camera_mode(cid, vision_cfg, _camera_keys(cid, synced)) == 'off':
                vision_cfg['camera_modes'][cid] = vision_cfg['mode']
            _save_local_vision_config(vision_cfg)

            previous = _local_autotrack_snapshot()
            try:
                if previous.get('enabled') and previous.get('camera') and previous['camera'] != cid:
                    result = stop_camera(previous['camera'])
                    if not result.get('success'):
                        raise RuntimeError(str(result.get('error') or 'Previous camera did not accept the PTZ stop'))
                with _LOCAL_AUTOTRACK_LOCK:
                    if previous.get('enabled') and previous.get('camera') and previous['camera'] != cid:
                        previous_cid = previous['camera']
                        _cancel_local_tracking_stop(previous_cid)
                        _LOCAL_TRACKING_RUNTIME.pop(previous_cid, None)
                    _LOCAL_AUTOTRACK.update(enabled=True, camera=cid, mode='vision')
                    _LOCAL_TRACKING_RUNTIME.setdefault(cid, {
                        'direction': 'stop',
                        'speed': 0,
                        'last_command_at': 0.0,
                        'last_direction': '',
                        'filtered_center': None,
                        'pending_direction': '',
                        'pending_count': 0,
                        'retry_after': 0.0,
                    })
            except Exception as exc:
                return jsonify(error=f'Unable to start local camera tracking: {exc}'), 502

            _invalidate_vision_cache()
            _ensure_port4000_worker()
            return jsonify({'ok': True, 'enabled': True, 'mode': 'vision', 'camera': cid, 'controller': 'local_ptz'})

        synced = _sync_cameras_with_confdeck(force=True)
        cid = _resolve_camera_alias(body.get('camera'))
        if not cid:
            cam_128 = next((c.get('id') for c in synced['cd_cameras'] if isinstance(c, dict) and c.get('ip') == '192.168.1.128'), '')
            cid = cam_128
        keys = _camera_keys(cid, synced)
        req_mode = str(body.get('mode') or ('off' if body.get('enabled') is False else 'vision')).strip()
        enabled = bool(body.get('enabled', req_mode != 'off')) and req_mode != 'off'

        if enabled:
            vision_cfg = _local_vision_config(synced)
            vision_cfg = {**vision_cfg, 'camera_modes': dict(vision_cfg['camera_modes'])}
            mode_changed = False
            if vision_cfg.get('mode', 'off') == 'off':
                vision_cfg['mode'] = 'advisory'
                mode_changed = True
            cam_modes = dict(vision_cfg.get('camera_modes') or {})
            if _effective_camera_mode(cid, vision_cfg, keys) == 'off':
                cam_modes[cid] = vision_cfg['mode']
                vision_cfg['camera_modes'] = cam_modes
                mode_changed = True
            if mode_changed:
                _save_local_vision_config(vision_cfg)
            rois = list(vision_cfg.get('rois') or [])
            if req_mode == 'vision' and not any(isinstance(r, dict) and r.get('camera') in keys for r in rois):
                # Ensure default full-frame Auto Track ROI exists when tracking starts with 0 ROIs
                default_roi = {'camera': cid, 'preset': '', 'seat': 'Auto Track', 'rect': [0, 0, 320, 180], 'masks': [], 'kind': 'seat'}
                rois.append(default_roi)
                vision_cfg['rois'] = rois
                r_out, r_code = _save_vision_rois(
                    vision_cfg,
                    action='add',
                    cid=cid,
                    preset='',
                    roi=default_roi,
                    synced=synced,
                )
                if r_code >= 400:
                    return jsonify(r_out), r_code

        data, code = _call_confdeck(
            'accuracy/vision/track',
            body={'camera': cid, 'mode': req_mode if enabled else 'off', 'enabled': enabled},
            method='POST',
            timeout=15,
        )
        if code in (404, 405):
            data, code = _call_confdeck(
                'accuracy/vision/autotrack',
                body={'camera': cid, 'mode': req_mode if enabled else 'off', 'enabled': enabled},
                method='POST',
                timeout=15,
            )
        _invalidate_vision_cache()
        _sync_cameras_with_confdeck(force=True)
        _ensure_port4000_worker()
        return jsonify(data), code

    if resource == 'vision/frame' and request.method == 'POST':
        cid = _resolve_camera_alias(body.get('camera'))
        preset = str(body.get('preset') or '')
        pixels_b64 = str(body.get('pixels') or '')
        data, code = _submit_vision_frame(cid, preset, pixels_b64, source='client', alias=str(body.get('camera') or cid))
        return jsonify(data), code

    if resource == 'vision/camera-moving' and request.method == 'POST':
        cid = _resolve_camera_alias(body.get('camera'))
        if cid:
            sec = float(body.get('seconds', 1.2) or 1.2)
            _MOVING_UNTIL[cid] = max(_MOVING_UNTIL.get(cid, 0.0), time.monotonic() + sec)
        return jsonify({'ok': True, 'camera': cid})

    if _PORT4000_VISION_LOCAL_ONLY:
        return jsonify(error='This operation is not available in Port 4000 local mode'), 501

    payload = dict(body) if body else None
    if payload and payload.get('camera'):
        payload['camera'] = _resolve_camera_alias(payload['camera'])
    target = resource
    if request.args:
        args = dict(request.args.items())
        if args.get('camera'):
            args['camera'] = _resolve_camera_alias(args['camera'])
        target = f"{resource}?{urlencode(args)}"
    timeout = 15 if any(k in resource for k in ('calibrate', 'autotrack', 'track')) else 4
    data, code = _call_confdeck(target, body=payload, method=request.method, timeout=timeout)
    if request.method in ('POST', 'PUT', 'DELETE'):
        _invalidate_vision_cache()
    return jsonify(data), code


def _schedule_startup_camera_sync():
    if 'pytest' in sys.modules or os.environ.get('PYTEST_CURRENT_TEST') or os.environ.get('PORT4000_DISABLE_VISION_WORKER') == '1':
        return
    def _retry_sync():
        if _PORT4000_VISION_LOCAL_ONLY:
            try:
                from services.port4000_mic_service import get_port4000_mic_service

                get_port4000_mic_service().ensure_started()
            except (RuntimeError, TimeoutError, OSError) as exc:
                print(f'[SENNHEISER] Port 4000 microphone runtime failed to start: {exc}', flush=True)
        for delay in (2.0, 4.0, 6.0):
            time.sleep(delay)
            try:
                res = _sync_cameras_with_confdeck(force=True)
                if res.get('status', 502) < 400:
                    _ensure_port4000_worker()
                    return
            except Exception:
                pass
    threading.Thread(target=_retry_sync, daemon=True).start()


_schedule_startup_camera_sync()


@sennheiser_bp.route('/api/sennheiser/<path:resource>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def endpoint(resource):
    # Allow sub-paths like cameras/<id>/presets and full Port 4000 AI Vision bridge
    parts = resource.split('/')
    if parts[0] == 'accuracy':
        return _handle_accuracy_resource('/'.join(parts[1:]))

    base_match = (request.method, resource) in ALLOWED or (request.method == 'GET' and parts[0] == 'cameras') or (parts[0] == 'microphones')
    if not base_match and (request.method, resource) not in ALLOWED:
        return jsonify(error=f'Unknown Sennheiser operation: {resource}'), 404

    try:
        body = request.get_json(silent=True) if request.method != 'GET' else None
        source = request.args.get('source')
        if source and (resource not in ('mic/device-settings', 'mic/device-settings/revert') or not re.fullmatch(r'[a-z0-9_-]{1,40}', source)):
            return jsonify(error='Invalid microphone selection'), 400
        if _PORT4000_VISION_LOCAL_ONLY:
            result, status = _local_microphone_operation(resource, request.method, body or {}, source)
            if status >= 400:
                return jsonify(result), status
        else:
            return jsonify(error='ConfDeck HTTP proxying is disabled on Port 4000'), 501

        if resource == 'state' and request.method == 'GET':
            cameras = result['settings']['cameras']
            cameras.extend(camera_catalog(cameras))

        # When saving tracking/settings, refresh OmniTrack's driver seat cache
        if resource == 'tracking/settings' and request.method == 'PUT':
            try:
                if state.controller_driver and hasattr(state.controller_driver, 'get_all_seats'):
                    seats = state.controller_driver.get_all_seats()
                    state.MIC_DATA_CACHE = seats
                    from core.config import MIC_DATA_FILE
                    from pathlib import Path
                    path = Path(MIC_DATA_FILE)
                    temporary = path.with_suffix('.json.new')
                    temporary.write_text(json.dumps(seats, indent=2))
                    temporary.replace(path)
            except Exception as e:
                print(f"[SENNHEISER] Error updating driver seats: {e}", flush=True)

        return jsonify(result)
    except ValueError as err:
        return jsonify(error=str(err)), 400
    except (RuntimeError, OSError) as err:
        return jsonify(error=f'Port 4000 microphone runtime is unavailable: {err}'), 503

