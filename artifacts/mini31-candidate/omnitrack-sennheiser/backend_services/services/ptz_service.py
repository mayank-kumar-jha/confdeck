import os
import sys
import json
import socket
import time
import uuid
import hashlib
import base64
import datetime
import subprocess
import threading
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from core.config import BASE_DIR, CAMERA_CONFIG_FILE, CAMERA_MAP, CAMERA_RTSP_FILE
from core import state
from core.helpers import load_driver_module

IMAGE_SETTINGS_FILE = os.path.join(BASE_DIR, 'camera_image_settings.json')

# Standard VISCA over IP ports (including 5678 for Solvision/Minrray, 52381 for Lumens/Canon/Sony, 1259 for PTZOptics)
UDP_PORTS = [5678, 52381, 1259, 52380, 2000, 5000]
TCP_PORTS = [5678, 1259, 52381]
TCP_PORT_GENERIC = 1259

_seq_lock = threading.Lock()
_global_sequence_number = 1

def _get_next_sequence():
    global _global_sequence_number
    with _seq_lock:
        seq = _global_sequence_number
        _global_sequence_number = (_global_sequence_number + 1) & 0xFFFFFFFF
        return seq

def get_driver_name_for_brand(brand):
    """
    Case-insensitive driver name lookup for camera brand.
    """
    brand_norm = (brand or '').strip().upper()
    for k, v in CAMERA_MAP.items():
        if k.strip().upper() == brand_norm:
            return v
    if "CANON" in brand_norm:
        return "driver_camera_canon"
    if "AVER" in brand_norm:
        return "driver_camera_aver"
    if "SONY" in brand_norm:
        return "driver_camera_sony"
    if "SOLVISION" in brand_norm or "MINRRAY" in brand_norm or "VALUEHD" in brand_norm:
        return "driver_camera_visca_udp"
    return "driver_camera_visca_udp"

def send_visca_packet(camera_ip, visca_body, target_port=None):
    """
    Transmits both RAW VISCA (required by Solvision, Minrray, ValueHD) and 
    Sony Encapsulated VISCA (required by Lumens, Canon, Sony) across UDP and TCP.
    """
    if not camera_ip:
        return False

    seq = _get_next_sequence()
    length_bytes = len(visca_body).to_bytes(2, 'big')
    seq_bytes = seq.to_bytes(4, 'big')
    full_packet = b'\x01\x00' + length_bytes + seq_bytes + visca_body

    udp_targets = []
    if target_port:
        try:
            p_int = int(target_port)
            if p_int > 0 and p_int not in udp_targets:
                udp_targets.append(p_int)
        except Exception:
            pass
    for p in UDP_PORTS:
        if p not in udp_targets:
            udp_targets.append(p)

    sent = False

    # 1. UDP Send: Transmit BOTH Raw VISCA (for Solvision) and Sony Encapsulated (for Lumens/Sony/Canon)
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.3)
        for p in udp_targets:
            try:
                # Raw VISCA (Direct 0x81 payload for Solvision)
                sock.sendto(visca_body, (camera_ip, p))
                # Encapsulated VISCA (0x01 0x00 header for Sony/Lumens)
                sock.sendto(full_packet, (camera_ip, p))
                sent = True
            except Exception:
                pass
    except Exception:
        pass
    finally:
        if sock:
            sock.close()

    # 2. TCP Send (Fast 100ms connect fallback)
    tcp_targets = []
    if target_port:
        try:
            p_int = int(target_port)
            if p_int > 0 and p_int not in tcp_targets:
                tcp_targets.append(p_int)
        except Exception:
            pass
    for p in TCP_PORTS:
        if p not in tcp_targets:
            tcp_targets.append(p)

    for p in tcp_targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.1)
            if s.connect_ex((camera_ip, p)) == 0:
                s.sendall(visca_body)
                s.sendall(full_packet)
                sent = True
            s.close()
        except Exception:
            pass

    return sent

def _send_onvif_ptz_soap(ip, port, user, pwd, action, body_xml):
    """Sends pure SOAP ONVIF PTZ command to camera without external dependencies."""
    if not ip:
        return False
    ports = [int(port)] if port and str(port).isdigit() else []
    for p in [80, 8080, 8899, 8000, 2000, 5000]:
        if p not in ports:
            ports.append(p)

    nonce_raw = uuid.uuid4().bytes
    nonce_b64 = base64.b64encode(nonce_raw).decode()
    created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    pwd_str = str(pwd or '')
    digest = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + pwd_str.encode()).digest()
    ).decode()

    ws_header = f"""<wsse:Security
  xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
  xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  <wsse:UsernameToken>
    <wsse:Username>{user or 'admin'}</wsse:Username>
    <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
    <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
    <wsu:Created>{created}</wsu:Created>
  </wsse:UsernameToken>
</wsse:Security>"""

    candidate_tokens = ["Profile_1", "Profile1", "token1", "000", "0", "profile_1"]
    ptz_paths = ["/onvif/ptz_service", "/onvif/ptz", "/onvif/services", "/onvif/device_service"]

    for p in ports[:3]:
        for path in ptz_paths:
            url = f"http://{ip}:{p}{path}"
            for token in candidate_tokens[:2]:
                envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope
  xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
  xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <SOAP-ENV:Header>{ws_header}</SOAP-ENV:Header>
  <SOAP-ENV:Body>
    {body_xml.replace('{PROFILE_TOKEN}', token)}
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
                for content_type in ['application/soap+xml; charset=utf-8', 'text/xml; charset=utf-8']:
                    headers = {
                        "Content-Type": content_type,
                        "SOAPAction": f'"{action}"',
                        "User-Agent": "ONVIF-Client/2.0"
                    }
                    try:
                        resp = requests.post(url, data=envelope, headers=headers, timeout=0.8, verify=False)
                        if resp.status_code == 200:
                            return True
                    except Exception:
                        pass
    return False

def _async_onvif_move(info, direction, speed=5):
    def _run():
        try:
            ip = info.get('ip')
            if not ip: return
            port = info.get('port')
            user = info.get('username') or 'admin'
            pwd = info.get('password') or 'admin'
            spd = float(speed) / 10.0
            x_val, y_val = 0.0, 0.0
            if 'left' in direction: x_val = -spd
            elif 'right' in direction: x_val = spd
            if 'up' in direction: y_val = spd
            elif 'down' in direction: y_val = -spd
            body = f"""<tptz:ContinuousMove>
  <tptz:ProfileToken>{{PROFILE_TOKEN}}</tptz:ProfileToken>
  <tptz:Velocity>
    <tt:PanTilt x="{x_val}" y="{y_val}"/>
  </tptz:Velocity>
</tptz:ContinuousMove>"""
            _send_onvif_ptz_soap(ip, port, user, pwd, "http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove", body)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

def _async_onvif_stop(info):
    def _run():
        try:
            ip = info.get('ip')
            if not ip: return
            port = info.get('port')
            user = info.get('username') or 'admin'
            pwd = info.get('password') or 'admin'
            body = """<tptz:Stop>
  <tptz:ProfileToken>{PROFILE_TOKEN}</tptz:ProfileToken>
  <tptz:PanTilt>true</tptz:PanTilt>
  <tptz:Zoom>true</tptz:Zoom>
</tptz:Stop>"""
            _send_onvif_ptz_soap(ip, port, user, pwd, "http://www.onvif.org/ver20/ptz/wsdl/Stop", body)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

def _async_onvif_goto_preset(info, preset_int):
    def _run():
        try:
            ip = info.get('ip')
            if not ip: return
            port = info.get('port')
            user = info.get('username') or 'admin'
            pwd = info.get('password') or 'admin'
            body = f"""<tptz:GotoPreset>
  <tptz:ProfileToken>{{PROFILE_TOKEN}}</tptz:ProfileToken>
  <tptz:PresetToken>{preset_int}</tptz:PresetToken>
</tptz:GotoPreset>"""
            _send_onvif_ptz_soap(ip, port, user, pwd, "http://www.onvif.org/ver20/ptz/wsdl/GotoPreset", body)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

def _async_onvif_set_preset(info, preset_int):
    def _run():
        try:
            ip = info.get('ip')
            if not ip: return
            port = info.get('port')
            user = info.get('username') or 'admin'
            pwd = info.get('password') or 'admin'
            body = f"""<tptz:SetPreset>
  <tptz:ProfileToken>{{PROFILE_TOKEN}}</tptz:ProfileToken>
  <tptz:PresetName>Preset {preset_int}</tptz:PresetName>
  <tptz:PresetToken>{preset_int}</tptz:PresetToken>
</tptz:SetPreset>"""
            _send_onvif_ptz_soap(ip, port, user, pwd, "http://www.onvif.org/ver20/ptz/wsdl/SetPreset", body)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

def get_camera_info(camera_key):
    """
    Retrieves camera configuration for camera_key (e.g. 'camera_1')
    """
    config_dict = state.camera_config or {}
    cam = config_dict.get(camera_key, {})
    if (not cam or not cam.get('ip')) and os.path.exists(CAMERA_CONFIG_FILE):
        try:
            with open(CAMERA_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                config_dict = saved.get('camera_config', {})
                cam = config_dict.get(camera_key, {})
        except Exception:
            pass

    return {
        'key': camera_key,
        'cam_name': cam.get('cam_name', camera_key.replace('_', ' ').title()),
        'brand': cam.get('brand', 'LUMENS'),
        'ip': cam.get('ip', '').strip(),
        'port': cam.get('port', ''),
        'username': cam.get('username', 'admin'),
        'password': cam.get('password', 'admin123' if (cam.get('brand') or '').upper() == 'CANON' else 'admin'),
        'protocol': cam.get('protocol', 'RTSP'),
        'rtsp_url': cam.get('rtsp_url', ''),
        'usb_path': cam.get('usb_path', ''),
        'home_preset': cam.get('home_preset', 1)
    }

# LUMENS cameras use one VISCA/IP transport. A short renewable lease ensures
# a lost browser release cannot leave continuous movement running indefinitely.
_lumens_lock = threading.RLock()
_lumens_timers = {}

def _lumens_packet(ip, payload):
    seq = _get_next_sequence().to_bytes(4, 'big')
    packet = b'\x01\x00' + len(payload).to_bytes(2, 'big') + seq + payload
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(packet, (ip, 52381))

def _lumens_stop_locked(camera_key, ip):
    timer = _lumens_timers.pop(camera_key, None)
    if timer:
        timer.cancel()
    _lumens_packet(ip, bytes.fromhex('81 01 06 01 05 05 03 03 FF'))

def _lumens_expire(camera_key, ip, token):
    with _lumens_lock:
        if _lumens_timers.get(camera_key) is token:
            _lumens_stop_locked(camera_key, ip)

def _lumens_move(camera_key, ip, body, duration=0.7):
    duration = max(0.08, min(0.7, float(duration)))
    with _lumens_lock:
        old = _lumens_timers.pop(camera_key, None)
        if old:
            old.cancel()
        # Arm before sending, including if the send fails.
        timer = threading.Timer(duration, lambda: _lumens_expire(camera_key, ip, timer))
        timer.daemon = True
        _lumens_timers[camera_key] = timer
        timer.start()
        _lumens_packet(ip, body)

def _lumens_stop(camera_key, ip):
    with _lumens_lock:
        _lumens_stop_locked(camera_key, ip)

# ------------------------------------------------------------------------------
# PTZ Movement (Pan / Tilt / Drag)
# ------------------------------------------------------------------------------
def move_camera(camera_key, direction, speed=5, duration=None, pan_speed=None, tilt_speed=None):
    """
    Moves the camera in the specified direction with given speed (1 to 10),
    or explicit VISCA pan_speed (1..24) / tilt_speed (1..20).
    Directions: 'up', 'down', 'left', 'right', 'up_left', 'up_right', 'down_left', 'down_right'
    For Lumens cameras, duration limits the movement lease in seconds.
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    port = info.get('port')
    brand = (info.get('brand') or 'LUMENS').upper()

    if not ip:
        return {'success': False, 'error': f"Camera {camera_key} has no IP configured"}

    # Clamp speed 1..10 and map to VISCA pan (1..24) and tilt (1..20)
    speed = max(1, min(10, int(speed)))
    if pan_speed is None:
        pan_speed = max(1, min(24, int(speed * 2.4)))
    else:
        pan_speed = max(1, min(24, int(pan_speed)))
    if tilt_speed is None:
        tilt_speed = max(1, min(20, int(tilt_speed)))
    else:
        tilt_speed = max(1, min(20, int(tilt_speed)))

    # Aver Driver Hook
    if brand == "AVER":
        try:
            mod = load_driver_module("driver_camera_aver")
            if mod:
                aver_speed = max(1, min(24, pan_speed))
                func_map = {
                    'up': getattr(mod, 'move_up', None),
                    'down': getattr(mod, 'move_down', None),
                    'left': getattr(mod, 'move_left', None),
                    'right': getattr(mod, 'move_right', None),
                    'up_left': getattr(mod, 'move_up_left', None),
                    'up_right': getattr(mod, 'move_up_right', None),
                    'down_left': getattr(mod, 'move_down_left', None),
                    'down_right': getattr(mod, 'move_down_right', None),
                }
                fn = func_map.get(direction)
                if fn:
                    fn(ip, aver_speed)
                    return {'success': True, 'camera': camera_key, 'action': direction, 'speed': speed}
        except Exception as e:
            print(f"[PTZ] Aver driver move failed, falling back to VISCA: {e}")

    # Standard VISCA Pan/Tilt Drive
    # Command: 81 01 06 01 VV WW Direction1 Direction2 FF
    dir_bytes = {
        'up': (0x03, 0x01),
        'down': (0x03, 0x02),
        'left': (0x01, 0x03),
        'right': (0x02, 0x03),
        'up_left': (0x01, 0x01),
        'up_right': (0x02, 0x01),
        'down_left': (0x01, 0x02),
        'down_right': (0x02, 0x02),
    }

    if direction not in dir_bytes:
        return {'success': False, 'error': f"Invalid direction '{direction}'"}

    d1, d2 = dir_bytes[direction]
    visca_body = bytes([0x81, 0x01, 0x06, 0x01, pan_speed, tilt_speed, d1, d2, 0xFF])

    if brand == "LUMENS":
        _lumens_move(camera_key, ip, visca_body, duration=0.7 if duration is None else duration)
        return {'success': True, 'camera': camera_key, 'action': direction, 'status': 'sent'}

    # For Canon: send through Canon driver pipeline (port 52381 / 1259)
    if brand == "CANON":
        try:
            mod = load_driver_module("driver_camera_canon")
            if mod and hasattr(mod, 'send_raw_visca'):
                mod.send_raw_visca(ip, visca_body)
        except Exception:
            pass

    # Universal Driver Hook (covers Solvision, PTZOptics, Lumens, etc.)
    driver_name = get_driver_name_for_brand(brand)
    try:
        mod = load_driver_module(driver_name)
        if mod and hasattr(mod, 'move'):
            try:
                mod.move(ip, direction, speed=speed, port=port)
            except TypeError:
                mod.move(ip, direction, speed=speed)
    except Exception:
        pass

    res = send_visca_packet(ip, visca_body, target_port=port)

    # Secondary ONVIF continuous move safety net (in background)
    _async_onvif_move(info, direction, speed)

    return {'success': True, 'camera': camera_key, 'action': direction, 'speed': speed, 'packet_sent': res}

def stop_camera(camera_key):
    """
    Sends PTZ Stop command.
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    port = info.get('port')
    brand = (info.get('brand') or 'LUMENS').upper()

    if not ip:
        return {'success': False, 'error': f"Camera {camera_key} has no IP configured"}

    if brand == "LUMENS":
        _lumens_stop(camera_key, ip)
        return {'success': True, 'camera': camera_key, 'action': 'stop', 'status': 'sent'}

    if brand == "AVER":
        try:
            mod = load_driver_module("driver_camera_aver")
            if mod and hasattr(mod, 'stop_ptz'):
                mod.stop_ptz(ip)
                return {'success': True, 'camera': camera_key, 'action': 'stop'}
        except Exception:
            pass

    # Standard VISCA Stop: 81 01 06 01 VV WW 03 03 FF
    visca_body = bytes([0x81, 0x01, 0x06, 0x01, 0x05, 0x05, 0x03, 0x03, 0xFF])

    if brand == "CANON":
        try:
            mod = load_driver_module("driver_camera_canon")
            if mod and hasattr(mod, 'send_raw_visca'):
                mod.send_raw_visca(ip, visca_body)
        except Exception:
            pass

    driver_name = get_driver_name_for_brand(brand)
    try:
        mod = load_driver_module(driver_name)
        if mod and hasattr(mod, 'stop_ptz'):
            try:
                mod.stop_ptz(ip, port=port)
            except TypeError:
                mod.stop_ptz(ip)
    except Exception:
        pass

    res = send_visca_packet(ip, visca_body, target_port=port)

    # Async ONVIF Stop
    _async_onvif_stop(info)

    return {'success': True, 'camera': camera_key, 'action': 'stop', 'packet_sent': res}

def home_camera(camera_key):
    """
    Sends camera to Home position (or configured home preset).
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    port = info.get('port')
    brand = (info.get('brand') or 'LUMENS').upper()
    home_p = info.get('home_preset', 1)

    if not ip:
        return {'success': False, 'error': f"Camera {camera_key} has no IP configured"}

    # Canon Home Interceptor: Preset 0 in driver_camera_canon maps to configured home preset with delayed unfreeze
    if brand == "CANON":
        try:
            mod = load_driver_module("driver_camera_canon")
            if mod and hasattr(mod, 'recall_preset'):
                mod.recall_preset(ip, 0)
                print(f"[PTZ] Canon camera {camera_key} Home preset recalled via driver_camera_canon")
                return {'success': True, 'camera': camera_key, 'action': 'home', 'brand': 'CANON'}
        except Exception as e:
            print(f"[PTZ] Canon Home error: {e}")

    if brand == "AVER":
        try:
            mod = load_driver_module("driver_camera_aver")
            if mod and hasattr(mod, 'home'):
                mod.home(ip)
                return {'success': True, 'camera': camera_key, 'action': 'home'}
        except Exception:
            pass

    driver_name = get_driver_name_for_brand(brand)
    try:
        mod = load_driver_module(driver_name)
        if mod and hasattr(mod, 'home'):
            try:
                mod.home(ip, port=port)
            except TypeError:
                mod.home(ip)
    except Exception:
        pass

    # Standard VISCA Home: 81 01 06 04 FF
    visca_body = bytes([0x81, 0x01, 0x06, 0x04, 0xFF])
    res = send_visca_packet(ip, visca_body, target_port=port)

    # Also recall configured home preset
    try:
        recall_preset(camera_key, home_p)
    except Exception:
        pass

    return {'success': True, 'camera': camera_key, 'action': 'home', 'packet_sent': res}

# ------------------------------------------------------------------------------
# Zoom Control
# ------------------------------------------------------------------------------
def zoom_camera(camera_key, action, speed=3):
    """
    Controls zoom: 'in' (Tele), 'out' (Wide), 'stop'
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    port = info.get('port')
    brand = (info.get('brand') or 'LUMENS').upper()

    if not ip:
        return {'success': False, 'error': f"Camera {camera_key} has no IP configured"}

    speed = max(0, min(7, int(speed)))

    if brand == "AVER":
        try:
            mod = load_driver_module("driver_camera_aver")
            if mod:
                if action == 'in' and hasattr(mod, 'zoom_in'):
                    mod.zoom_in(ip, speed)
                    return {'success': True, 'camera': camera_key, 'action': 'zoom_in'}
                elif action == 'out' and hasattr(mod, 'zoom_out'):
                    mod.zoom_out(ip, speed)
                    return {'success': True, 'camera': camera_key, 'action': 'zoom_out'}
                elif action == 'stop' and hasattr(mod, 'stop_zoom'):
                    mod.stop_zoom(ip)
                    return {'success': True, 'camera': camera_key, 'action': 'stop_zoom'}
        except Exception:
            pass

    # Standard VISCA Zoom
    # Tele (In): 81 01 04 07 2p FF
    # Wide (Out): 81 01 04 07 3p FF
    # Stop: 81 01 04 07 00 FF
    if action == 'in':
        cmd_byte = 0x20 | (speed & 0x07)
        visca_body = bytes([0x81, 0x01, 0x04, 0x07, cmd_byte, 0xFF])
    elif action == 'out':
        cmd_byte = 0x30 | (speed & 0x07)
        visca_body = bytes([0x81, 0x01, 0x04, 0x07, cmd_byte, 0xFF])
    else:  # stop
        visca_body = bytes([0x81, 0x01, 0x04, 0x07, 0x00, 0xFF])

    if brand == "CANON":
        try:
            mod = load_driver_module("driver_camera_canon")
            if mod and hasattr(mod, 'send_raw_visca'):
                mod.send_raw_visca(ip, visca_body)
        except Exception:
            pass

    driver_name = get_driver_name_for_brand(brand)
    try:
        mod = load_driver_module(driver_name)
        if mod and hasattr(mod, 'zoom'):
            try:
                mod.zoom(ip, action, speed=speed, port=port)
            except TypeError:
                mod.zoom(ip, action, speed=speed)
    except Exception:
        pass

    res = send_visca_packet(ip, visca_body, target_port=port)
    return {'success': True, 'camera': camera_key, 'action': f'zoom_{action}', 'packet_sent': res}

# ------------------------------------------------------------------------------
# Presets (Recall & Save)
# ------------------------------------------------------------------------------
def recall_preset(camera_key, preset_number):
    """
    Recalls preset position (2 to 100 for Canon, 1 to 254 for standard).
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    port = info.get('port')
    brand = (info.get('brand') or 'LUMENS').upper()

    if not ip:
        return {'success': False, 'error': f"Camera {camera_key} has no IP configured"}

    try:
        preset_int = int(preset_number)
    except Exception:
        return {'success': False, 'error': "Invalid preset number"}

    if brand == "LUMENS":
        if not 1 <= preset_int <= 127:
            return {'success': False, 'error': 'Preset must be between 1 and 127'}
        with _lumens_lock:
            _lumens_stop_locked(camera_key, ip)
            _lumens_packet(ip, bytes([0x81, 0x01, 0x04, 0x3F, 2, preset_int, 0xFF]))
        return {'success': True, 'camera': camera_key, 'preset': preset_int, 'status': 'sent; camera arrival not verified'}

    # Canon cameras strictly use presets 2 to 100
    if brand == "CANON":
        preset_int = max(2, min(100, preset_int))
    else:
        preset_int = max(1, min(254, preset_int))

    driver_name = get_driver_name_for_brand(brand)
    driver_success = False
    try:
        mod = load_driver_module(driver_name)
        if mod and hasattr(mod, 'recall_preset'):
            try:
                driver_success = bool(mod.recall_preset(ip, preset_int, port=port))
            except TypeError:
                driver_success = bool(mod.recall_preset(ip, preset_int))
            print(f"[PTZ] Recalled Preset {preset_int} on {camera_key} ({brand} @ {ip}) via driver {driver_name} (result: {driver_success})")
    except Exception as e:
        print(f"[PTZ] Driver recall_preset failed: {e}")

    # Fallback / Dual transmission to standard VISCA: 81 01 04 3F 02 pp FF
    # (sends both Raw VISCA for Solvision and Sony Encapsulated for Lumens/Canon to all target ports)
    visca_body = bytes([0x81, 0x01, 0x04, 0x3F, 0x02, preset_int & 0xFF, 0xFF])
    res = send_visca_packet(ip, visca_body, target_port=port)
    print(f"[PTZ] Recalled Preset {preset_int} on {camera_key} via direct VISCA dispatch")

    # Secondary ONVIF GotoPreset safety net (background)
    _async_onvif_goto_preset(info, preset_int)

    return {'success': True, 'camera': camera_key, 'preset': preset_int, 'driver': driver_name, 'packet_sent': res or driver_success}

def save_preset(camera_key, preset_number):
    """
    Saves current camera position to preset (2 to 100 for Canon, 1 to 254 for standard).
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    port = info.get('port')
    brand = (info.get('brand') or 'LUMENS').upper()

    if not ip:
        return {'success': False, 'error': f"Camera {camera_key} has no IP configured"}

    try:
        preset_int = int(preset_number)
    except Exception:
        return {'success': False, 'error': "Invalid preset number"}

    if brand == "LUMENS":
        if not 1 <= preset_int <= 127:
            return {'success': False, 'error': 'Preset must be between 1 and 127'}
        with _lumens_lock:
            _lumens_stop_locked(camera_key, ip)
            _lumens_packet(ip, bytes([0x81, 0x01, 0x04, 0x3F, 1, preset_int, 0xFF]))
        return {'success': True, 'camera': camera_key, 'preset': preset_int, 'status': 'sent; camera arrival not verified'}

    # Canon cameras strictly use presets 2 to 100
    if brand == "CANON":
        preset_int = max(2, min(100, preset_int))
    else:
        preset_int = max(1, min(254, preset_int))

    driver_name = get_driver_name_for_brand(brand)
    driver_success = False
    try:
        mod = load_driver_module(driver_name)
        if mod and hasattr(mod, 'save_preset'):
            try:
                driver_success = bool(mod.save_preset(ip, preset_int, port=port))
            except TypeError:
                driver_success = bool(mod.save_preset(ip, preset_int))
            print(f"[PTZ] Saved Preset {preset_int} on {camera_key} ({brand} @ {ip}) via driver {driver_name} (result: {driver_success})")
    except Exception as e:
        print(f"[PTZ] Driver save_preset failed: {e}")

    # Fallback / Dual transmission to standard VISCA: 81 01 04 3F 01 pp FF
    # (sends both Raw VISCA for Solvision and Sony Encapsulated for Lumens/Canon to all target ports)
    visca_body = bytes([0x81, 0x01, 0x04, 0x3F, 0x01, preset_int & 0xFF, 0xFF])
    res = send_visca_packet(ip, visca_body, target_port=port)
    print(f"[PTZ] Saved Preset {preset_int} on {camera_key} via direct VISCA dispatch")

    # Secondary ONVIF SetPreset safety net (background)
    _async_onvif_set_preset(info, preset_int)

    return {'success': True, 'camera': camera_key, 'preset': preset_int, 'driver': driver_name, 'packet_sent': res or driver_success}

# ------------------------------------------------------------------------------
# Hardware Image Calibration (Brightness, Contrast, Sharpness, Saturation)
# Direct ISP hardware manipulation across VISCA over IP, ONVIF Imaging & V4L2 USB
# ------------------------------------------------------------------------------

def _send_visca_imaging(camera_ip, brand, brightness=None, contrast=None, sharpness=None, saturation=None):
    """
    Sends VISCA hardware image calibration packets to the camera DSP chip.
    Targets UDP 52381 (Canon / Sony) and TCP 1259.
    """
    if not camera_ip:
        return False

    is_canon = (brand or '').upper() == 'CANON'
    canon_mod = None
    if is_canon:
        canon_mod = load_driver_module("driver_camera_canon")

    def _dispatch(body):
        if is_canon and canon_mod and hasattr(canon_mod, 'send_raw_visca'):
            try:
                canon_mod.send_raw_visca(camera_ip, body)
            except Exception:
                pass
        send_visca_packet(camera_ip, body)

    try:
        # 1. Brightness / Exposure Compensation
        if brightness is not None:
            # A. VISCA Brightness Direct (0x00 to 0x1F, 0 to 31)
            b_val = max(0, min(31, int((brightness / 100.0) * 31)))
            p = (b_val >> 4) & 0x0F
            q = b_val & 0x0F
            _dispatch(bytes([0x81, 0x01, 0x04, 0x4D, 0x00, 0x00, p, q, 0xFF]))

            # B. VISCA Exposure Comp ON + Direct (0 to 14, 7 neutral)
            _dispatch(bytes([0x81, 0x01, 0x04, 0x3E, 0x02, 0xFF]))  # Exposure Comp ON
            exp_val = max(0, min(14, int((brightness / 100.0) * 14)))
            p_exp = (exp_val >> 4) & 0x0F
            q_exp = exp_val & 0x0F
            _dispatch(bytes([0x81, 0x01, 0x04, 0x4E, 0x00, 0x00, p_exp, q_exp, 0xFF]))

        # 2. Sharpness (Aperture Direct: 0 to 15)
        if sharpness is not None:
            s_val = max(0, min(15, int((sharpness / 100.0) * 15)))
            p = (s_val >> 4) & 0x0F
            q = s_val & 0x0F
            _dispatch(bytes([0x81, 0x01, 0x04, 0x42, 0x00, 0x00, p, q, 0xFF]))

        # 3. Saturation (Color Gain Direct: 0 to 14)
        if saturation is not None:
            sat_val = max(0, min(14, int((saturation / 100.0) * 14)))
            p = (sat_val >> 4) & 0x0F
            q = sat_val & 0x0F
            _dispatch(bytes([0x81, 0x01, 0x04, 0x49, 0x00, 0x00, p, q, 0xFF]))
            _dispatch(bytes([0x81, 0x01, 0x04, 0x49, 0x00, 0x00, 0x00, q, 0xFF]))

        # 4. Contrast
        if contrast is not None:
            c_val = max(0, min(15, int((contrast / 100.0) * 15)))
            p = (c_val >> 4) & 0x0F
            q = c_val & 0x0F
            _dispatch(bytes([0x81, 0x01, 0x04, 0xA2, 0x00, 0x00, p, q, 0xFF]))

        return True
    except Exception as e:
        print(f"[PTZ] VISCA imaging packet error on {camera_ip}: {e}")
        return False

def _send_onvif_imaging(ip, port, user, pwd, brightness=None, contrast=None, sharpness=None, saturation=None):
    """
    Sends pure SOAP ONVIF SetImagingSettings to camera hardware.
    Zero external dependencies needed (no zeep, no onvif library).
    """
    if not ip:
        return False

    ports = [int(port)] if port and str(port).isdigit() else []
    for p in [80, 2000, 8080, 8000, 8899, 443]:
        if p not in ports:
            ports.append(p)

    # Build WS-Security Header
    nonce_raw = uuid.uuid4().bytes
    nonce_b64 = base64.b64encode(nonce_raw).decode()
    created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    pwd_str = str(pwd or '')
    digest = base64.b64encode(
        hashlib.sha1(nonce_raw + created.encode() + pwd_str.encode()).digest()
    ).decode()

    ws_header = f"""<wsse:Security
  xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
  xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  <wsse:UsernameToken>
    <wsse:Username>{user or 'admin'}</wsse:Username>
    <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
    <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
    <wsu:Created>{created}</wsu:Created>
  </wsse:UsernameToken>
</wsse:Security>"""

    # Build imaging settings elements
    settings_xml = []
    if brightness is not None:
        settings_xml.append(f"<tt:Brightness>{float(brightness):.1f}</tt:Brightness>")
    if saturation is not None:
        settings_xml.append(f"<tt:ColorSaturation>{float(saturation):.1f}</tt:ColorSaturation>")
    if contrast is not None:
        settings_xml.append(f"<tt:Contrast>{float(contrast):.1f}</tt:Contrast>")
    if sharpness is not None:
        settings_xml.append(f"<tt:Sharpness>{float(sharpness):.1f}</tt:Sharpness>")

    if not settings_xml:
        return False

    settings_inner = "".join(settings_xml)

    candidate_tokens = ["VideoSourceToken", "000", "0", "VideoSource_1", "Profile_1", "token1"]

    action = "http://www.onvif.org/ver20/imaging/wsdl/SetImagingSettings"
    imaging_paths = [
        "/onvif/imaging_service",
        "/onvif/imaging",
        "/onvif/services",
        "/onvif/device_service"
    ]

    for p in ports[:3]:
        for path in imaging_paths:
            url = f"http://{ip}:{p}{path}"
            for token in candidate_tokens[:2]:
                envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope
  xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
  xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <SOAP-ENV:Header>{ws_header}</SOAP-ENV:Header>
  <SOAP-ENV:Body>
    <timg:SetImagingSettings>
      <timg:VideoSourceToken>{token}</timg:VideoSourceToken>
      <timg:ImagingSettings>
        {settings_inner}
      </timg:ImagingSettings>
      <timg:ForcePersistence>true</timg:ForcePersistence>
    </timg:SetImagingSettings>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
                for content_type in ['application/soap+xml; charset=utf-8', 'text/xml; charset=utf-8']:
                    headers = {
                        "Content-Type": content_type,
                        "SOAPAction": f'"{action}"',
                        "User-Agent": "ONVIF-Client/2.0"
                    }
                    try:
                        resp = requests.post(url, data=envelope, headers=headers, timeout=1.2, verify=False)
                        if resp.status_code == 200:
                            print(f"[PTZ] ONVIF SetImagingSettings applied successfully on {url}")
                            return True
                    except Exception:
                        pass
    return False

def _send_v4l2_imaging(usb_path, brightness=None, contrast=None, sharpness=None, saturation=None):
    """
    Applies hardware controls to USB camera via Linux v4l2-ctl.
    """
    if sys.platform != 'linux' or not usb_path or not os.path.exists(usb_path):
        return False
    try:
        ctrls = []
        if brightness is not None:
            ctrls.append(f"brightness={int((brightness / 100.0) * 255)}")
        if contrast is not None:
            ctrls.append(f"contrast={int((contrast / 100.0) * 255)}")
        if saturation is not None:
            ctrls.append(f"saturation={int((saturation / 100.0) * 255)}")
        if sharpness is not None:
            ctrls.append(f"sharpness={int((sharpness / 100.0) * 255)}")
        if ctrls:
            subprocess.run(["v4l2-ctl", "-d", usb_path, f"--set-ctrl={','.join(ctrls)}"], timeout=1)
            print(f"[PTZ] V4L2 hardware controls applied to {usb_path}: {ctrls}")
            return True
    except Exception as e:
        print(f"[PTZ] V4L2 imaging error on {usb_path}: {e}")
    return False

def _send_canon_cgi_imaging(ip, user, pwd, brightness=None, contrast=None, sharpness=None, saturation=None):
    """
    Canon PTZ cameras (CR-N300, CR-N500) HTTP CGI imaging control.
    """
    if not ip:
        return False
    try:
        params = []
        if brightness is not None:
            params.append(f"brightness={int(brightness)}")
        if contrast is not None:
            params.append(f"contrast={int(contrast)}")
        if sharpness is not None:
            params.append(f"sharpness={int(sharpness)}")
        if saturation is not None:
            params.append(f"saturation={int(saturation)}")
        if params:
            url = f"http://{ip}/cgi-bin/camera?{'&'.join(params)}"
            requests.get(url, auth=(user, pwd), timeout=1.0)
            return True
    except Exception:
        pass
    return False

def apply_hardware_image_settings_async(camera_key, settings):
    """
    Background worker that broadcasts image calibration to all camera hardware protocols:
    1. VISCA over IP
    2. ONVIF SetImagingSettings
    3. Canon HTTP CGI
    4. V4L2 USB hardware controls
    """
    info = get_camera_info(camera_key)
    ip = info.get('ip')
    brand = (info.get('brand') or 'LUMENS').upper()
    port = info.get('port')
    user = info.get('username') or 'admin'
    pwd = info.get('password') or ('admin123' if brand == 'CANON' else 'admin')
    usb_path = info.get('usb_path')

    b = settings.get('brightness')
    c = settings.get('contrast')
    sh = settings.get('sharpness')
    sat = settings.get('saturation')

    # Layer 1: VISCA over IP
    if ip:
        _send_visca_imaging(ip, brand, brightness=b, contrast=c, sharpness=sh, saturation=sat)

    # Layer 2: ONVIF Imaging SOAP
    if ip:
        _send_onvif_imaging(ip, port, user, pwd, brightness=b, contrast=c, sharpness=sh, saturation=sat)

    # Layer 3: Canon CGI if Canon camera
    if ip and brand == 'CANON':
        _send_canon_cgi_imaging(ip, user, pwd, brightness=b, contrast=c, sharpness=sh, saturation=sat)

    # Layer 4: V4L2 for USB video capture
    if usb_path:
        _send_v4l2_imaging(usb_path, brightness=b, contrast=c, sharpness=sh, saturation=sat)

def load_all_image_settings():
    default_settings = {}
    for i in range(1, 10):
        default_settings[f"camera_{i}"] = {
            'brightness': 50,
            'sharpness': 26,
            'contrast': 25,
            'saturation': 50
        }
    if os.path.exists(IMAGE_SETTINGS_FILE):
        try:
            with open(IMAGE_SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                default_settings.update(saved)
        except Exception:
            pass
    return default_settings

def save_all_image_settings(data):
    try:
        with open(IMAGE_SETTINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[PTZ] Failed to save image settings: {e}")

def get_camera_image_settings(camera_key):
    settings = load_all_image_settings()
    return settings.get(camera_key, {
        'brightness': 50,
        'sharpness': 26,
        'contrast': 25,
        'saturation': 50
    })

def set_camera_image_settings(camera_key, brightness=None, contrast=None, sharpness=None, saturation=None):
    settings = load_all_image_settings()
    cam_settings = settings.get(camera_key, {
        'brightness': 50,
        'sharpness': 26,
        'contrast': 25,
        'saturation': 50
    })

    if brightness is not None:
        cam_settings['brightness'] = max(0, min(100, int(brightness)))
    if contrast is not None:
        cam_settings['contrast'] = max(0, min(100, int(contrast)))
    if sharpness is not None:
        cam_settings['sharpness'] = max(0, min(100, int(sharpness)))
    if saturation is not None:
        cam_settings['saturation'] = max(0, min(100, int(saturation)))

    settings[camera_key] = cam_settings
    save_all_image_settings(settings)

    # Spawn background worker to apply to camera hardware without stalling HTTP response
    threading.Thread(
        target=apply_hardware_image_settings_async,
        args=(camera_key, cam_settings),
        daemon=True
    ).start()

    return {'success': True, 'camera': camera_key, 'settings': cam_settings}

# ------------------------------------------------------------------------------
# Direct RTSP Camera Preview Stream Hub (Zero-Lag Ultra-Low Latency)
# ------------------------------------------------------------------------------
def get_camera_stream_url(camera_key):
    """
    Returns the RTSP URL for camera_key from active configuration.
    Automatically handles Canon CR-N /rtpstream/config3 path mapping.
    """
    info = get_camera_info(camera_key)
    brand = (info.get('brand') or '').upper()

    url = None

    # 1. Check camera_rtsp.json
    if os.path.exists(CAMERA_RTSP_FILE):
        try:
            with open(CAMERA_RTSP_FILE, 'r', encoding='utf-8') as f:
                rtsp_map = json.load(f)
                if camera_key in rtsp_map and rtsp_map[camera_key].get('rtsp_url'):
                    url = rtsp_map[camera_key]['rtsp_url'].strip()
        except Exception:
            pass

    # 2. Check camera_config.json
    if not url and os.path.exists(CAMERA_CONFIG_FILE):
        try:
            with open(CAMERA_CONFIG_FILE, 'r', encoding='utf-8') as f:
                cdata = json.load(f)
                cfg = cdata.get('camera_config', {}).get(camera_key, {})
                if cfg.get('rtsp_url'):
                    url = cfg['rtsp_url'].strip()
                elif cfg.get('ip'):
                    ip = cfg.get('ip', '').strip()
                    user = cfg.get('username') or 'admin'
                    pwd = cfg.get('password') or ('admin123' if brand == 'CANON' else 'admin')
                    port = str(cfg.get('port') or '554').strip() or '554'
                    path = '/rtpstream/config3' if brand == 'CANON' else '/stream1'
                    url = f"rtsp://{user}:{pwd}@{ip}:{port}{path}"
        except Exception:
            pass

    # 3. Check memory config
    if not url:
        ip = info.get('ip')
        if ip:
            user = 'admin'
            pwd = 'admin123' if brand == 'CANON' else 'admin'
            path = '/rtpstream/config3' if brand == 'CANON' else '/stream1'
            url = f"rtsp://{user}:{pwd}@{ip}:554{path}"

    # Canon auto-fix: Canon PTZ streams use /rtpstream/config3 or /rtpstream/config1
    if url and brand == 'CANON':
        if '/h264' in url:
            url = url.replace('/h264', '/rtpstream/config3')
        elif '/stream1' in url:
            url = url.replace('/stream1', '/rtpstream/config3')

    return url

class CameraPreviewHub:
    """
    Zero-latency real-time frame capture hub.
    Uses a non-sleeping grabber loop to continuously drain the RTSP socket buffer,
    ensuring live preview response is instantaneous (<50ms delay) when moving PTZ.
    Outputs authentic camera hardware video frames without artificial UI filters.
    """
    def __init__(self):
        self.latest_frames = {}
        self.lock = threading.Lock()
        self.threads = {}
        self.running = {}
        self.last_requested_time = {}
        self._cached_standby = {}

    def get_standby_frame(self, camera_key):
        """Generates a sleek 1280x720 connecting / standby frame for instant HTTP handshake."""
        with self.lock:
            if camera_key in self._cached_standby:
                return self._cached_standby[camera_key]

        info = get_camera_info(camera_key)
        brand = info.get('brand', 'CAMERA').upper()
        name = info.get('cam_name', camera_key).upper()
        ip = info.get('ip', '')

        try:
            import cv2
            import numpy as np
            img = np.zeros((720, 1280, 3), dtype=np.uint8)
            img[:] = (20, 16, 15)  # Slate dark matching UI
            cv2.putText(img, f"Connecting to {name} ({brand})...", (380, 340),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (245, 130, 32), 2, cv2.LINE_AA)
            if ip:
                cv2.putText(img, f"RTSP IP: {ip} | Port: 554", (450, 385),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 160, 160), 1, cv2.LINE_AA)
            ret, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 60])
            if ret:
                frame_bytes = buf.tobytes()
                with self.lock:
                    self._cached_standby[camera_key] = frame_bytes
                return frame_bytes
        except Exception:
            pass
        return b''

    def _worker(self, camera_key, rtsp_url):
        try:
            import cv2
        except ImportError:
            print("[PREVIEW HUB] OpenCV (cv2) not available", flush=True)
            with self.lock:
                self.running[camera_key] = False
            return

        print(f"[PREVIEW HUB] Connecting zero-lag RTSP feed for {camera_key}: {rtsp_url}", flush=True)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000|stimeout;3000000|reorder_queue_size;0"

        cap = None
        try:
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            fail_count = 0
            last_encode_time = 0

            while self.running.get(camera_key, False):
                # Idle timeout: stop if no viewer requested frames for 30 seconds
                idle_sec = time.time() - self.last_requested_time.get(camera_key, time.time())
                if idle_sec > 30.0:
                    print(f"[PREVIEW HUB] Idle timeout for {camera_key} (no viewers), closing capture.", flush=True)
                    break

                if not cap.isOpened():
                    time.sleep(0.5)
                    cap.open(rtsp_url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    fail_count += 1
                    if fail_count > 10:
                        break
                    continue

                # Read continuously to drain incoming RTSP frames from socket buffer
                ret, frame = cap.read()
                if not ret or frame is None:
                    fail_count += 1
                    if fail_count > 15:
                        cap.release()
                        time.sleep(0.5)
                        cap.open(rtsp_url, cv2.CAP_FFMPEG)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        fail_count = 0
                    time.sleep(0.01)
                    continue

                fail_count = 0

                # Rate-limit compression to ~30 FPS (0.032s)
                now = time.time()
                if now - last_encode_time < 0.032:
                    continue
                last_encode_time = now

                # Scale to 1280x720 for fast transfer and crisp display
                h, w = frame.shape[:2]
                if w > 1280:
                    frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)

                # Output authentic unmanipulated hardware frame directly from camera sensor
                ret_enc, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ret_enc:
                    with self.lock:
                        self.latest_frames[camera_key] = jpeg.tobytes()
        except Exception as e:
            print(f"[PREVIEW HUB] Exception in worker for {camera_key}: {e}", flush=True)
        finally:
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
            with self.lock:
                self.running[camera_key] = False
            print(f"[PREVIEW HUB] Closed RTSP feed for {camera_key}", flush=True)

    def touch(self, camera_key):
        self.last_requested_time[camera_key] = time.time()
        need_start = False
        with self.lock:
            existing_thread = self.threads.get(camera_key)
            is_alive = existing_thread and existing_thread.is_alive()
            if not is_alive or not self.running.get(camera_key, False):
                self.running[camera_key] = True
                need_start = True

        if need_start:
            rtsp_url = get_camera_stream_url(camera_key)
            if rtsp_url:
                t = threading.Thread(target=self._worker, args=(camera_key, rtsp_url), daemon=True)
                with self.lock:
                    self.threads[camera_key] = t
                t.start()
            else:
                with self.lock:
                    self.running[camera_key] = False

    def get_frame(self, camera_key):
        self.touch(camera_key)
        with self.lock:
            return self.latest_frames.get(camera_key)

preview_hub = CameraPreviewHub()
