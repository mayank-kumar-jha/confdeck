import json
import subprocess
import time
import os
import glob
import sys
import signal
from urllib.parse import urlsplit, urlunsplit

# Cache for hardware format checks so we don't query v4l2 repeatedly
_h264_cache = {}

def find_json_path():
    """
    Accurately find camera_rtsp.json across all deployment scenarios:
    1. Linux system deployment (/home/ordain/televic_controller_cam_feed_webrtc/backend_services/camera_rtsp.json)
    2. Local script directory (backend_services/camera_rtsp.json)
    3. Compiled Nuitka binary execution directory
    4. Project root / working directory
    """
    candidates = [
        "/home/ordain/televic_controller_cam_feed_webrtc/backend_services/camera_rtsp.json",
        "/home/ordain/omnitrack-pro/backend_services/camera_rtsp.json",
        os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "backend_services", "camera_rtsp.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_rtsp.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend_services", "camera_rtsp.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend_services", "camera_rtsp.json"),
        os.path.join(os.getcwd(), "backend_services", "camera_rtsp.json"),
        os.path.join(os.getcwd(), "camera_rtsp.json"),
        "/home/ordain/televic_controller_cam_feed_webrtc/camera_rtsp.json",
        "/home/ordain/omnitrack-pro/camera_rtsp.json"
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

JSON_PATH = find_json_path()
MEDIAMTX_RTSP_URL = "rtsp://127.0.0.1:8554"
CHECK_INTERVAL = 3  # Fast 3-second watchdog and recovery loop

active_processes = {}

def cleanup_all():
    """Terminate all active FFmpeg processes cleanly upon exit."""
    for cid, proc_info in list(active_processes.items()):
        try:
            proc_info["proc_obj"].kill()
            proc_info["proc_obj"].wait(timeout=1)
        except Exception:
            pass
    active_processes.clear()

def sig_handler(sig, frame):
    print("🛑 [Stream Manager] Shutting down cleanly...")
    cleanup_all()
    sys.exit(0)

signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)

def load_cameras():
    """Reads camera_rtsp.json safely without race conditions."""
    path = find_json_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def clean_rtsp_url(url):
    """Normalize RTSP URLs."""
    if not url:
        return ""
    if "2000:554" in url:
        url = url.replace("2000:554", "554")
    return url.strip()

def get_available_usb_capture_nodes():
    """
    Finds real video capture device nodes (e.g. /dev/video0, /dev/video4, ...),
    ignoring metadata nodes (which are odd index numbers like video1, video3, video5).
    """
    capture_nodes = []
    
    # 1. Primary: Check /dev/v4l/by-path/ for '-video-index0' (video capture node)
    by_path = '/dev/v4l/by-path'
    if os.path.exists(by_path):
        try:
            for f in sorted(os.listdir(by_path)):
                if f.endswith('-video-index0') or ('-video-index' not in f and '-index0' in f):
                    real = os.path.realpath(os.path.join(by_path, f))
                    if real not in capture_nodes:
                        capture_nodes.append(real)
        except Exception:
            pass

    # 2. Secondary: Check /dev/video* devices directly
    for p in sorted(glob.glob('/dev/video*')):
        try:
            num_str = p.replace('/dev/video', '')
            if num_str.isdigit():
                node_num = int(num_str)
                # In Linux UVC standard, capture nodes are even (0, 2, 4, 6, 8) and metadata nodes are odd (1, 3, 5, 7, 9)
                if node_num % 2 == 0:
                    real = os.path.realpath(p)
                    if real not in capture_nodes:
                        capture_nodes.append(real)
        except Exception:
            pass

    return capture_nodes

def supports_h264_capture(device_node):
    """
    Checks if a V4L2 device node supports native hardware H.264 output.
    Uses v4l2-ctl --list-formats. Caches result to avoid repeating checks.
    """
    global _h264_cache
    if device_node in _h264_cache:
        return _h264_cache[device_node]

    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "-d", device_node, "--list-formats"],
            stderr=subprocess.DEVNULL, universal_newlines=True, timeout=2
        )
        supported = ("'H264'" in out) or ("H.264" in out)
        _h264_cache[device_node] = supported
        return supported
    except Exception:
        # Default to True for high-end conference UHD cameras
        return True

def resolve_usb_device(requested_path, claimed_nodes):
    """
    Resolves the requested USB path to an active physical capture node:
    1. If the requested path exists and is unclaimed, use it.
    2. If the kernel switched symlink format (e.g., -usbv3- / -usbv2- changed to -usb-), normalize and check.
    3. If USB hub port numbers shifted after a cold boot, dynamically map to an available unclaimed capture node.
    """
    if requested_path:
        # 1. Exact path exists
        if os.path.exists(requested_path):
            real = os.path.realpath(requested_path)
            if real not in claimed_nodes:
                claimed_nodes.add(real)
                return real

        # 2. Kernel alias normalization (-usbv3- / -usbv2- <-> -usb-)
        alt1 = requested_path.replace("-usbv3-", "-usb-").replace("-usbv2-", "-usb-")
        if os.path.exists(alt1):
            real = os.path.realpath(alt1)
            if real not in claimed_nodes:
                claimed_nodes.add(real)
                return real

        alt2 = requested_path.replace("-usb-", "-usbv3-")
        if os.path.exists(alt2):
            real = os.path.realpath(alt2)
            if real not in claimed_nodes:
                claimed_nodes.add(real)
                return real

    # 3. Dynamic fallback to an available unclaimed capture node
    available = get_available_usb_capture_nodes()
    for node in available:
        if node not in claimed_nodes:
            claimed_nodes.add(node)
            return node

    return None

def browser_stream(stream_input):
    """Prefer an H.264 substream for an H.265 RTSP source, without changing settings."""
    def codec(url):
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-rtsp_transport', 'tcp', '-select_streams', 'v:0',
                 '-show_entries', 'stream=codec_name', '-of', 'default=nw=1:nk=1', url],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=6,
            )
            return result.stdout.strip().lower() if result.returncode == 0 else ''
        except (OSError, subprocess.TimeoutExpired):
            return ''

    if codec(stream_input) not in ('hevc', 'h265'):
        return stream_input, False
    parts = urlsplit(stream_input)
    alternate = {'/stream1': '/stream2', '/live/av0': '/live/av1'}.get(parts.path.lower())
    if alternate:
        candidate = urlunsplit((parts.scheme, parts.netloc, alternate, parts.query, parts.fragment))
        if codec(candidate) == 'h264':
            return candidate, False
    return stream_input, True


def spawn_ffmpeg(cam_id, stream_input, protocol="RTSP"):
    """
    Spawns FFmpeg for RTSP, RTMP, or USB.
    Uses DEVNULL for both stdout and stderr to prevent OS pipe buffer deadlocks.
    """
    if protocol == "USB":
        # Check if camera has native hardware H.264
        if supports_h264_capture(stream_input):
            # Native H.264 passthrough (0% CPU usage - perfectly smooth for 5+ cameras!)
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "v4l2", "-input_format", "h264", "-i", stream_input,
                "-c:v", "copy",
                "-f", "rtsp", "-rtsp_transport", "tcp",
                f"{MEDIAMTX_RTSP_URL}/{cam_id}"
            ]
        else:
            # Fallback for MJPEG-only cameras: Use Intel GPU (VAAPI) if present, else ultrafast CPU
            if os.path.exists("/dev/dri/renderD128"):
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-vaapi_device", "/dev/dri/renderD128",
                    "-f", "v4l2", "-input_format", "mjpeg", "-i", stream_input,
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "h264_vaapi",
                    "-f", "rtsp", "-rtsp_transport", "tcp",
                    f"{MEDIAMTX_RTSP_URL}/{cam_id}"
                ]
            else:
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-f", "v4l2", "-input_format", "mjpeg", "-i", stream_input,
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                    "-pix_fmt", "yuv420p", "-s", "1280x720", "-b:v", "2000k",
                    "-f", "rtsp", "-rtsp_transport", "tcp",
                    f"{MEDIAMTX_RTSP_URL}/{cam_id}"
                ]
    elif protocol == "RTMP" or stream_input.startswith("rtmp://"):
        # Pure clean RTMP relay (0% CPU copy)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", stream_input,
            "-c", "copy",
            "-f", "rtsp", "-rtsp_transport", "tcp",
            f"{MEDIAMTX_RTSP_URL}/{cam_id}"
        ]
    else:
        # Standard RTSP relay with TCP transport (0% CPU copy)
        stream_input, convert = browser_stream(stream_input)
        video_args = (['-map', '0:v:0', '-an', '-vf', 'scale=-2:720', '-c:v', 'libx264',
                       '-preset', 'ultrafast', '-tune', 'zerolatency', '-pix_fmt', 'yuv420p',
                       '-g', '25', '-bf', '0'] if convert else ['-c', 'copy'])
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", stream_input,
            *video_args,
            "-f", "rtsp", "-rtsp_transport", "tcp",
            f"{MEDIAMTX_RTSP_URL}/{cam_id}"
        ]

    try:
        # DEVNULL ensures process never hangs on full stderr pipe buffer
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"❌ [Stream Manager] Failed to start FFmpeg for {cam_id}: {e}")
        return None

def main():
    time.sleep(2)  # Settle delay
    current_json = find_json_path()
    print(f"🎬 [Stream Manager] Starting. Reading configuration from: {current_json}")
    
    while True:
        raw_config = load_cameras()
        if not raw_config:
            time.sleep(CHECK_INTERVAL)
            continue

        claimed_usb_nodes = set()

        for cam_id, info in raw_config.items():
            protocol = info.get("protocol", "RTSP")
            if protocol == "USB":
                raw_path = info.get("usb_path", "")
                if not raw_path:
                    continue
                target_url = resolve_usb_device(raw_path, claimed_usb_nodes)
                if not target_url:
                    if cam_id in active_processes:
                        try:
                            active_processes[cam_id]["proc_obj"].kill()
                            active_processes[cam_id]["proc_obj"].wait(timeout=1)
                        except Exception:
                            pass
                        del active_processes[cam_id]
                    continue
            else:
                raw_url = info.get("rtsp_url", "")
                if not raw_url:
                    continue
                target_url = clean_rtsp_url(raw_url)

            # 1. New Camera
            if cam_id not in active_processes:
                print(f"🎥 [Stream Manager] Starting FFmpeg for {cam_id}: {target_url} ({protocol})")
                p = spawn_ffmpeg(cam_id, target_url, protocol)
                if p:
                    active_processes[cam_id] = {"proc_obj": p, "target": target_url, "proto": protocol}
                
            # 2. Target URL / device changed
            elif active_processes[cam_id]["target"] != target_url:
                print(f"🔄 [Stream Manager] Target changed for {cam_id}. Restarting...")
                try:
                    active_processes[cam_id]["proc_obj"].kill()
                    active_processes[cam_id]["proc_obj"].wait(timeout=1)
                except Exception:
                    pass
                del active_processes[cam_id]
                
            # 3. If FFmpeg process exited or crashed (e.g. temporary glitch or camera reconnected)
            else:
                proc = active_processes[cam_id]["proc_obj"]
                if proc.poll() is not None:
                    print(f"⚠️ [Stream Manager] FFmpeg exited for {cam_id} (code {proc.poll()}). Auto-recovering...")
                    del active_processes[cam_id]

        # 4. Clean up stopped/deleted cameras
        dropped = [cid for cid in list(active_processes.keys()) if cid not in raw_config]
        for cam_id in dropped:
            print(f"🛑 [Stream Manager] Stopping {cam_id} (removed from config)")
            try:
                active_processes[cam_id]["proc_obj"].kill()
                active_processes[cam_id]["proc_obj"].wait(timeout=1)
            except Exception:
                pass
            del active_processes[cam_id]

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
