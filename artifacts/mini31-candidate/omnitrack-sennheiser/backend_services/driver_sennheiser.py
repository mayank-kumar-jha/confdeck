"""Translate ConfDeck seat decisions into OmniTrack microphone states.

Camera commands remain exclusively in OmniTrack's existing controller listener.
ConfDeck must stay in dry-run while this adapter is connected.
"""
import json
import time
import threading
from external_cameras import camera_catalog
from services.port4000_mic_service import get_port4000_mic_service

SEAT_SYNC_LOCK = threading.RLock()


def request_confdeck(path, body=None, method=None, timeout=3):
    return get_port4000_mic_service().request(path, body, method, timeout)


class ControllerDriver:
    voice_tracking = True
    def __init__(self, host_ip='', username='', password='', secondary_ip='', secondary_password='', controller_model='', **kwargs):
        self.controller_model = controller_model or kwargs.get('controller_model', '')
        self.host_ip = host_ip
        self.secondary_ip = secondary_ip or kwargs.get('secondary_ip', '')
        
        if 'Dual' in self.controller_model:
            if not self.host_ip:
                self.host_ip = '192.168.1.29'
            if not self.secondary_ip:
                self.secondary_ip = '192.168.1.92'

        self.connected = False
        self.connected_sources = []
        self.seats = []
        self.last_poll = 0
        self.states = {}

    def connect(self):
        service = get_port4000_mic_service()
        snapshot = service.snapshot()
        configured = [
            ('primary', snapshot['settings']['mic']),
            *(
                (mic['id'], mic)
                for mic in snapshot['settings'].get('microphones', [])
            ),
        ]
        requested_ips = [self.host_ip] if self.host_ip else []
        if 'Dual' in self.controller_model and self.secondary_ip:
            requested_ips.append(self.secondary_ip)
        if not requested_ips:
            return False
        selected_sources = []
        for requested_ip in requested_ips:
            match = next(
                (
                    (source, mic)
                    for source, mic in configured
                    if mic.get('ip') == requested_ip and mic.get('enabled')
                ),
                None,
            )
            if match is None:
                return False
            if match[0] not in selected_sources:
                selected_sources.append(match[0])

        service.connect_microphones(selected_sources)
        self.connected_sources = selected_sources
        self.get_all_seats()
        self.connected = True
        for _ in range(25):
            st = service.snapshot()
            states = {'primary': st.get('mic', {})}
            states.update({mic.get('id'): mic for mic in st.get('microphones', [])})
            if all(
                states.get(source, {}).get('state') == 'Connected'
                for source in selected_sources
            ):
                return True
            time.sleep(0.25)
        self.connected = False
        self.connected_sources = []
        return False

    def disconnect(self):
        if self.connected_sources:
            get_port4000_mic_service().disconnect_microphones(self.connected_sources)
        self.connected = False
        self.connected_sources = []
        self.states = {}

    def get_all_seats(self):
        with SEAT_SYNC_LOCK:
            return self._sync_seats()

    def _sync_seats(self):
        from pathlib import Path
        from core import state
        from core.config import CAMERA_CONFIG_FILE, MAPPING_FILE, MIC_DATA_FILE
        snapshot = get_port4000_mic_service().snapshot()
        settings = snapshot['settings']
        primary = settings['tracking']['zones']
        camera_config = json.loads(Path(CAMERA_CONFIG_FILE).read_text()).get('camera_config', {})
        by_ip = {camera.get('ip'): key.split('_')[-1] for key, camera in camera_config.items() if camera.get('ip')}
        cameras = settings['cameras']
        sources = {camera['id']: camera for camera in [*cameras, *camera_catalog(cameras)]}
        path = Path(MAPPING_FILE)
        previous = json.loads(path.read_text()) if path.exists() else []
        existing = {row['serial']: row for row in previous}
        mappings = [row for row in previous if not row['serial'].startswith('sennheiser:')]
        seats = []
        # Wide is an explicit engine decision, never a fallback for lost confidence.
        wide = settings['tracking'].get('wide_presets', {})
        if any(sources.get(cid, {}).get('ip') in by_ip for cid in wide):
            primary = [*primary, {'id': 'wide', 'name': 'Wide shot', 'presets': wide}]
        for zone in primary:
            base = 'sennheiser:' + zone['id']
            targets = []
            for identity, preset in zone.get('presets', {}).items():
                ip = sources.get(identity, {}).get('ip')
                if ip in by_ip:
                    targets.append((by_ip[ip], str(preset)))
            if not targets:
                old = existing.get(base, {})
                targets = [(old.get('camera', ''), old.get('preset', ''))]
            for camera, preset in targets:
                serial = base if len(targets) == 1 else base + ':camera_' + camera
                seat_number = str(len(seats) + 1)
                seats.append({'serial': serial, 'zone_id': zone['id'], 'seatNumber': seat_number, 'name': zone['name'], 'mic_id': zone.get('mic_id', 'primary')})
                mappings.append({'serial': serial, 'person_name': zone['name'], 'camera': camera, 'preset': preset})
        temporary = path.with_suffix('.json.new')
        temporary.write_text(json.dumps(mappings, indent=2))
        temporary.replace(path)
        self.seats = seats
        for mapping in (state.mic_to_camera, state.mic_to_preset, state.mic_to_name):
            for key in list(mapping):
                if key.startswith('sennheiser:'):
                    mapping.pop(key, None)
        for row in mappings:
            state.mic_to_camera[row['serial']] = row['camera']
            state.mic_to_preset[row['serial']] = row['preset']
            state.mic_to_name[row['serial']] = row['person_name']
        mic_data_path = Path(MIC_DATA_FILE)
        temporary_mic_data = mic_data_path.with_suffix('.json.new')
        temporary_mic_data.write_text(json.dumps(seats, indent=2), encoding='utf-8')
        temporary_mic_data.replace(mic_data_path)
        state.MIC_DATA_CACHE = seats
        return self.seats

    def get_realtime_states(self):
        if not self.connected:
            return {}
        if time.monotonic() - self.last_poll < 0.15:
            return dict(self.states)
        try:
            snapshot = get_port4000_mic_service().snapshot()
        except Exception:
            self.states = {}
            self.last_poll = time.monotonic()
            return {}
        tracking = snapshot['tracking']
        if snapshot.get('far_end', {}).get('blocked'):
            self.states = {}
            self.last_poll = time.monotonic()
            return {}
        # Fail closed rather than allowing two engines to drive the cameras.
        if tracking.get('live'):
            self.connected = False
            raise RuntimeError('Disable ConfDeck live camera control before using OmniTrack tracking')
        active = tracking.get('current')
        source = next((s.get('mic_id', 'primary') for s in self.seats
                       if s['zone_id'] == active and active != 'wide'),
                      tracking.get('active_mic') or 'primary')
        mic = snapshot.get('mic', {}) if source == 'primary' else next(
            (m for m in snapshot.get('microphones', []) if m.get('id') == source), {})
        healthy = mic.get('state') == 'Connected' and mic.get('age') is not None and mic['age'] <= 3 and not mic.get('stale_fields')
        if not healthy:
            # Missing data is not a microphone-OFF event or a camera decision.
            self.states = {}
            self.last_poll = time.monotonic()
            return {}
        self.states = {s['seatNumber']: s['zone_id'] == active for s in self.seats}
        self.last_poll = time.monotonic()
        return dict(self.states)

    def set_mic_state(self, seat_number, enabled):
        # Ceiling seats are inferred positions, not individually switchable microphones.
        return False

    def wait_for_event(self):
        time.sleep(0.15)
