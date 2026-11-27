import os
import json
import time
from functools import wraps

def synchronized_mode(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with state.tracking_lock:
            return fn(*args, **kwargs)
    return wrapped

from core.config import CAMERA_LAYOUT_FILE, CAMERA_RTSP_FILE, LAYOUT_MODE_FILE
from core import state

_socketio = None

def init_layout_service(socketio_instance):
    global _socketio
    _socketio = socketio_instance

def emit_socketio(event, data=None, room=None, broadcast=False):
    if not _socketio:
        return
    try:
        if room:
            _socketio.emit(event, data or {}, room=room)
        elif broadcast:
            _socketio.emit(event, data or {}, broadcast=True)
        else:
            _socketio.emit(event, data or {})
    except Exception:
        pass

def get_layout_config():
    default_layout = {
        "layout_type": "single",
        "slots": [
            {"slot_id": 1, "assigned_camera": None, "custom_label": ""},
            {"slot_id": 2, "assigned_camera": None, "custom_label": ""},
            {"slot_id": 3, "assigned_camera": None, "custom_label": ""},
            {"slot_id": 4, "assigned_camera": None, "custom_label": ""},
            {"slot_id": 5, "assigned_camera": None, "custom_label": ""},
            {"slot_id": 6, "assigned_camera": None, "custom_label": ""}
        ]
    }
    try:
        if os.path.exists(CAMERA_LAYOUT_FILE):
            with open(CAMERA_LAYOUT_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading layout config: {e}")
    return default_layout

def get_rtsp_config():
    try:
        if os.path.exists(CAMERA_RTSP_FILE):
            with open(CAMERA_RTSP_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading RTSP config: {e}")
    return {}

def broadcast_layout_update():
    """Broadcast layout changes to all connected viewers perfectly"""
    if not _socketio:
        return
    layout_data = get_layout_config()
    rtsp_data = get_rtsp_config()
    
    with state.lock:
        viewers_copy = state.connected_viewers.copy()
        
    for sid in viewers_copy:
        try:
            _socketio.emit('layout_update', {
                'layout': layout_data,
                'rtsp_urls': rtsp_data
            }, room=sid)
        except Exception as e:
            print(f"Error broadcasting to {sid}: {e}")

@synchronized_mode
def save_layout_mode_config(mode=None, sub_mode=None):
    """Save layout mode and sub-mode to file and update global state"""
    try:
        current_mode = 'manual'
        current_sub_mode = 'single'
        if os.path.exists(LAYOUT_MODE_FILE):
            try:
                with open(LAYOUT_MODE_FILE, 'r') as f:
                    data = json.load(f)
                    current_mode = data.get('mode', 'manual')
                    current_sub_mode = data.get('sub_mode', 'single')
            except Exception:
                pass
        
        if mode is not None:
            current_mode = mode
            state.auto_layout_enabled = (mode == 'auto')
        if sub_mode is not None:
            current_sub_mode = sub_mode
            state.auto_sub_mode = sub_mode
            
        temporary = str(LAYOUT_MODE_FILE) + '.new'
        with open(temporary, 'w') as f:
            json.dump({
                'mode': current_mode,
                'sub_mode': current_sub_mode,
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S')
            }, f, indent=2)
        os.replace(temporary, LAYOUT_MODE_FILE)
        return True
    except Exception as e:
        print(f"Error saving layout mode config: {e}")
        return False

@synchronized_mode
def load_layout_mode_config():
    """Load layout mode and sub-mode from file and update global state"""
    try:
        if os.path.exists(LAYOUT_MODE_FILE):
            with open(LAYOUT_MODE_FILE, 'r') as f:
                data = json.load(f)
                mode = data.get('mode', 'manual')
                sub_mode = data.get('sub_mode', 'single')
                state.auto_layout_enabled = (mode == 'auto')
                if sub_mode in ['single', 'dynamic_dual', 'dynamic_triple']:
                    state.auto_sub_mode = sub_mode
                return mode, state.auto_sub_mode
    except Exception as e:
        print(f"Error loading layout mode config: {e}")
    return 'manual', 'single'

def save_layout_mode(mode):
    return save_layout_mode_config(mode=mode)

def get_layout_mode():
    mode, _ = load_layout_mode_config()
    return mode

class SpeakerPreviewHold:
    """Hold the last voice-tracked preview during a short gap, not a new speaker."""
    def __init__(self, seconds=5.0):
        self.seconds = seconds
        self.reset()

    def reset(self):
        self.last_serial = None
        self.gap_started = None

    def select(self, active, mapping, voice_connected, now):
        if not voice_connected:
            self.reset()
            return active[-1] if active else None
        if active:
            self.last_serial = active[-1]
            self.gap_started = None
            return self.last_serial
        if self.last_serial is None or not mapping.get(self.last_serial):
            self.reset()
            return None
        if self.gap_started is None:
            self.gap_started = now
        if now - self.gap_started < self.seconds:
            return self.last_serial
        self.reset()
        return None


def auto_switching_engine():
    from services.controller_service import load_mic_data, is_voice_controller
    print("[AUTO ENGINE] Multi-Mode Core tracking thread actively monitoring room states.")
    
    last_applied_layout_type = None
    last_applied_slots = []
    
    delegate_queue = []
    delegate_off_timestamps = {}
    DEBOUNCE_TIME = 0.5
    
    triple_slot2_delegate = None
    triple_slot3_delegate = None
    triple_next_slot = 2
    last_sub_mode = None
    preview_hold = SpeakerPreviewHold()
    
    while True:
        if state.is_resetting:
            time.sleep(0.5)
            continue
            
        load_layout_mode_config()

        if state.auto_layout_enabled:
            try:
                if state.auto_sub_mode != last_sub_mode:
                    preview_hold.reset()
                    triple_slot2_delegate = None
                    triple_slot3_delegate = None
                    triple_next_slot = 2
                    last_sub_mode = state.auto_sub_mode

                current_raw_active = [serial for serial, is_on in state.previous_mic_states.items() if is_on]
                
                target_layout_type = "single"
                slots = []

                # 🟢 MODE 1: SINGLE VIEW
                if state.auto_sub_mode == 'single':
                    target_layout_type = "single"
                    target_camera_key = "camera_1"
                    label_text = ""
                    
                    voice_connected = (is_voice_controller() and state.is_connected
                                       and bool(getattr(state.controller_driver, 'connected', True)))
                    latest_speaker_serial = preview_hold.select(
                        current_raw_active, state.mic_to_camera, voice_connected, time.monotonic())
                    if latest_speaker_serial:
                        assigned_camera_id = state.mic_to_camera.get(latest_speaker_serial)
                        p_name = state.mic_to_name.get(latest_speaker_serial, "")
                        person_name = str(p_name).strip() if p_name else ""
                        
                        if assigned_camera_id and str(assigned_camera_id) != '0':
                            target_camera_key = f"camera_{assigned_camera_id}"
                            label_text = person_name
                        else:
                            target_camera_key = "camera_1"
                            label_text = person_name
                    
                    slots = [{"slot_id": 1, "assigned_camera": target_camera_key, "custom_label": label_text}]

                # 🔵 MODE 2: DYNAMIC 50:50 VIEW (CHAIRMAN + LATEST DELEGATE)
                elif state.auto_sub_mode == 'dynamic_dual':
                    chairman_serial = None
                    mics = load_mic_data()
                    for m in mics:
                        if str(m.get('role', '')).strip().lower() == "chairman":
                            chairman_serial = m.get('serial')
                            break
                            
                    current_active_delegates = [s for s in current_raw_active if s != chairman_serial]
                    
                    for s in current_active_delegates:
                        if s not in delegate_queue:
                            delegate_queue.append(s)
                        if s in delegate_off_timestamps:
                            del delegate_off_timestamps[s]
                            
                    mics_to_remove = []
                    for s in delegate_queue:
                        if s not in current_active_delegates:
                            if s not in delegate_off_timestamps:
                                delegate_off_timestamps[s] = time.time()
                            elif time.time() - delegate_off_timestamps[s] > DEBOUNCE_TIME:
                                mics_to_remove.append(s)
                                
                    for s in mics_to_remove:
                        delegate_queue.remove(s)
                        del delegate_off_timestamps[s]

                    ch_cam = "camera_1"
                    ch_name = ""
                    if chairman_serial:
                        c_id = state.mic_to_camera.get(chairman_serial, 1)
                        ch_cam = f"camera_{c_id}" if c_id else "camera_1"
                        c_n = state.mic_to_name.get(chairman_serial, "")
                        ch_name = str(c_n).strip() if c_n else ""

                    if len(delegate_queue) == 0:
                        target_layout_type = "single"
                        slots = [{"slot_id": 1, "assigned_camera": ch_cam, "custom_label": ch_name}]
                    else:
                        target_layout_type = "dual"
                        latest_delegate = delegate_queue[-1]
                        
                        del_cam_id = state.mic_to_camera.get(latest_delegate, 1)
                        d_n = state.mic_to_name.get(latest_delegate, "")
                        del_name = str(d_n).strip() if d_n else ""
                        
                        slots = [
                            {"slot_id": 1, "assigned_camera": ch_cam, "custom_label": ch_name},
                            {"slot_id": 2, "assigned_camera": f"camera_{del_cam_id}", "custom_label": del_name}
                        ]

                # 🟣 MODE 3: TRIPLE VIEW (DYNAMIC 3-WAY: CHAIRMAN + 2 ACTIVE DELEGATES)
                elif state.auto_sub_mode == 'dynamic_triple':
                    chairman_serial = None
                    mics = load_mic_data()
                    for m in mics:
                        if str(m.get('role', '')).strip().lower() == "chairman":
                            chairman_serial = m.get('serial')
                            break

                    current_active_delegates = [s for s in current_raw_active if s != chairman_serial]

                    newly_added_delegates = []
                    for s in current_active_delegates:
                        if s not in delegate_queue:
                            delegate_queue.append(s)
                            newly_added_delegates.append(s)
                        if s in delegate_off_timestamps:
                            del delegate_off_timestamps[s]

                    mics_to_remove = []
                    for s in delegate_queue:
                        if s not in current_active_delegates:
                            if s not in delegate_off_timestamps:
                                delegate_off_timestamps[s] = time.time()
                            elif time.time() - delegate_off_timestamps[s] > DEBOUNCE_TIME:
                                mics_to_remove.append(s)

                    for s in mics_to_remove:
                        delegate_queue.remove(s)
                        del delegate_off_timestamps[s]

                    ch_cam = "camera_1"
                    ch_name = ""
                    if chairman_serial:
                        c_id = state.mic_to_camera.get(chairman_serial, 1)
                        ch_cam = f"camera_{c_id}" if c_id else "camera_1"
                        c_n = state.mic_to_name.get(chairman_serial, "")
                        ch_name = str(c_n).strip() if c_n else ""

                    del_count = len(delegate_queue)

                    if del_count == 0:
                        target_layout_type = "single"
                        slots = [{"slot_id": 1, "assigned_camera": ch_cam, "custom_label": ch_name}]
                        triple_slot2_delegate = None
                        triple_slot3_delegate = None
                        triple_next_slot = 2

                    elif del_count == 1:
                        target_layout_type = "dual"
                        del1 = delegate_queue[0]
                        del1_cam_id = state.mic_to_camera.get(del1, 1)
                        del1_cam = f"camera_{del1_cam_id}" if del1_cam_id else "camera_1"
                        del1_n = state.mic_to_name.get(del1, "")
                        del1_name = str(del1_n).strip() if del1_n else ""

                        slots = [
                            {"slot_id": 1, "assigned_camera": ch_cam, "custom_label": ch_name},
                            {"slot_id": 2, "assigned_camera": del1_cam, "custom_label": del1_name}
                        ]
                        triple_slot2_delegate = del1
                        triple_slot3_delegate = None
                        triple_next_slot = 3

                    elif del_count == 2:
                        target_layout_type = "triple"
                        if triple_slot2_delegate in delegate_queue and triple_slot3_delegate in delegate_queue:
                            pass
                        elif triple_slot2_delegate in delegate_queue:
                            triple_slot3_delegate = next(s for s in delegate_queue if s != triple_slot2_delegate)
                        elif triple_slot3_delegate in delegate_queue:
                            triple_slot2_delegate = next(s for s in delegate_queue if s != triple_slot3_delegate)
                        else:
                            triple_slot2_delegate = delegate_queue[0]
                            triple_slot3_delegate = delegate_queue[1]
                        triple_next_slot = 2

                        s2_cam_id = state.mic_to_camera.get(triple_slot2_delegate, 1) if triple_slot2_delegate else 1
                        s2_cam = f"camera_{s2_cam_id}" if s2_cam_id else "camera_1"
                        s2_n = state.mic_to_name.get(triple_slot2_delegate, "") if triple_slot2_delegate else ""
                        s2_name = str(s2_n).strip() if s2_n else ""

                        s3_cam_id = state.mic_to_camera.get(triple_slot3_delegate, 1) if triple_slot3_delegate else 1
                        s3_cam = f"camera_{s3_cam_id}" if s3_cam_id else "camera_1"
                        s3_n = state.mic_to_name.get(triple_slot3_delegate, "") if triple_slot3_delegate else ""
                        s3_name = str(s3_n).strip() if s3_n else ""

                        slots = [
                            {"slot_id": 1, "assigned_camera": ch_cam, "custom_label": ch_name},
                            {"slot_id": 2, "assigned_camera": s2_cam, "custom_label": s2_name},
                            {"slot_id": 3, "assigned_camera": s3_cam, "custom_label": s3_name}
                        ]

                    else:
                        target_layout_type = "triple"

                        for new_del in newly_added_delegates:
                            if new_del == triple_slot2_delegate or new_del == triple_slot3_delegate:
                                continue
                            if triple_next_slot == 2:
                                triple_slot2_delegate = new_del
                                triple_next_slot = 3
                            else:
                                triple_slot3_delegate = new_del
                                triple_next_slot = 2

                        if triple_slot2_delegate not in delegate_queue:
                            available = [s for s in delegate_queue if s != triple_slot3_delegate]
                            triple_slot2_delegate = available[-1] if available else None
                        if triple_slot3_delegate not in delegate_queue:
                            available = [s for s in delegate_queue if s != triple_slot2_delegate]
                            triple_slot3_delegate = available[-1] if available else None

                        s2_cam_id = state.mic_to_camera.get(triple_slot2_delegate, 1) if triple_slot2_delegate else 1
                        s2_cam = f"camera_{s2_cam_id}" if s2_cam_id else "camera_1"
                        s2_n = state.mic_to_name.get(triple_slot2_delegate, "") if triple_slot2_delegate else ""
                        s2_name = str(s2_n).strip() if s2_n else ""

                        s3_cam_id = state.mic_to_camera.get(triple_slot3_delegate, 1) if triple_slot3_delegate else 1
                        s3_cam = f"camera_{s3_cam_id}" if s3_cam_id else "camera_1"
                        s3_n = state.mic_to_name.get(triple_slot3_delegate, "") if triple_slot3_delegate else ""
                        s3_name = str(s3_n).strip() if s3_n else ""

                        slots = [
                            {"slot_id": 1, "assigned_camera": ch_cam, "custom_label": ch_name},
                            {"slot_id": 2, "assigned_camera": s2_cam, "custom_label": s2_name},
                            {"slot_id": 3, "assigned_camera": s3_cam, "custom_label": s3_name}
                        ]

                # Apply Changes if needed
                slots_changed = False
                if len(slots) != len(last_applied_slots):
                    slots_changed = True
                else:
                    for i in range(len(slots)):
                        if slots[i] != last_applied_slots[i]:
                            slots_changed = True
                            break

                if (target_layout_type != last_applied_layout_type) or slots_changed:
                    print(f"🎥 [AUTO ENGINE] Cutting layout to: {target_layout_type.upper()}")
                    layout_data = {"layout_type": target_layout_type, "slots": slots}
                    
                    with open(CAMERA_LAYOUT_FILE, 'w') as f:
                        json.dump(layout_data, f, indent=2)
                    
                    broadcast_layout_update()
                    
                    last_applied_layout_type = target_layout_type
                    last_applied_slots = slots
                    
            except Exception as e:
                print(f"❌ Auto engine loop exception: {e}")
        else:
            preview_hold.reset()
            last_applied_layout_type = None
            last_applied_slots = []
                
        time.sleep(0.02)
