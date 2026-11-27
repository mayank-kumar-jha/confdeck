"""Interactive laptop webcam test for 106-pt neural Lip-Sync + Super-Seat Zoom.

Automatically serves a live dual-stream browser UI at http://127.0.0.1:4567
(works cleanly even with opencv-python-headless; no cv2.imshow GUI needed!).

Run directly:
    python scripts/test_lipsync_webcam.py
"""
import sys
import threading
import time
import webbrowser
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from confdeck.vision.mouth import SeatVisionDetector

HTML_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>2d106det Lip-Sync & Super-Seat Auto-Zoom Webcam Test</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
    .bar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
    h1 { font-size: 18px; margin: 0; }
    .pill { background: #1e293b; border: 1px solid #334155; padding: 6px 12px; border-radius: 999px; font-size: 13px; }
    button { background: #f58220; color: #fff; border: none; border-radius: 8px; padding: 9px 16px; font-weight: 700; cursor: pointer; }
    button:hover { background: #ea580c; }
    .grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 12px; }
    .card h2 { font-size: 14px; color: #cbd5e1; margin: 0 0 8px; }
    img { width: 100%; border-radius: 8px; background: #020617; display: block; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #334155; }
  </style>
</head>
<body>
  <div class="bar">
    <h1>Super-Seat Neural Lip-Sync &amp; Auto-Zoom Test (InsightFace 2d106det)</h1>
    <div style="display:flex;gap:10px;align-items:center;">
      <span id="status" class="pill">Starting camera…</span>
      <button onclick="resetLock()">Reset Lock &amp; Zoom</button>
    </div>
  </div>
  <div class="grid">
    <div class="card">
      <h2>1. Group View (20 Neural Lip Landmarks + Probabilities)</h2>
      <img src="/stream/group" alt="Group View">
    </div>
    <div class="card">
      <h2>2. Locked Speaker Portrait (Smooth Pan + Auto-Zoom)</h2>
      <img src="/stream/portrait" alt="Portrait View">
    </div>
  </div>
  <div class="card" style="margin-top:16px;">
    <h2>Tracked Faces Telemetry</h2>
    <table>
      <thead><tr><th>Track ID</th><th>Status</th><th>Inner MAR</th><th>Speech Prob</th><th>Face Height</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <script>
    async function resetLock() {
      await fetch('/reset', { method: 'POST' });
    }
    setInterval(async () => {
      try {
        const d = await (await fetch('/telemetry')).json();
        document.getElementById('status').textContent =
          `Model: ${d.model} | ${d.ms.toFixed(1)} ms/frame | Zoom: ${d.zoom.toFixed(2)}x | ${d.reason}`;
        const tbody = document.getElementById('rows');
        tbody.innerHTML = (d.tracks || []).map(t => {
          const locked = (t.track_id === d.locked_id);
          const badge = locked ? '🔒 LOCKED SPEAKER' : (t.speaking ? '🗣️ Speaking' : 'Quiet');
          return `<tr><td>#${t.track_id}</td><td><b>${badge}</b></td><td>${t.inner_mar.toFixed(3)}</td><td>${(t.speech_prob*100).toFixed(0)}%</td><td>${t.box[3]} px</td></tr>`;
        }).join('') || '<tr><td colspan="5">No faces detected yet</td></tr>';
      } catch (e) {}
    }, 150);
  </script>
</body>
</html>
"""


class WebcamLipSyncRunner:
    def __init__(self, camera_index: int = 0) -> None:
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open webcam index {camera_index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.detector = SeatVisionDetector()
        if self.detector.detector is None:
            raise RuntimeError(self.detector.warning or "YuNet face detector unavailable")
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.group_jpg = b""
        self.portrait_jpg = b""
        self.sim_zoom = 1.0
        self.smooth_cx = 160.0
        self.smooth_cy = 90.0
        self.telemetry = {
            "model": "insightface_2d106det"
            if (self.detector.group_selector and self.detector.group_selector.scorer.landmarks.available)
            else "flux_fallback",
            "ms": 0.0,
            "zoom": 1.0,
            "reason": "idle",
            "locked_id": None,
            "tracks": [],
        }
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def reset(self) -> None:
        with self.lock:
            self.detector.reset()
            self.sim_zoom = 1.0

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            ok, frame = self.cap.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue

            t0 = time.perf_counter()
            fh_full, fw_full = frame.shape[:2]
            ref_bgr = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
            ref_gray = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY)
            hi_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            with self.lock:
                now = time.monotonic()
                res = self.detector.evaluate(
                    ref_gray,
                    {"super_seat_group": {"rect": [0, 0, 320, 180]}},
                    now=now,
                    hi_res_frame=hi_gray,
                )
                group = res.get("group_speaker") or {}
                locked_id = group.get("locked_track_id")
                target_box = group.get("target_box")
                zoom_action = group.get("zoom_action", "none")

                if target_box is not None and group.get("reason") == "lip_sync_locked":
                    tx = target_box[0] + target_box[2] * 0.5
                    ty = target_box[1] + target_box[3] * 0.45
                    self.smooth_cx = 0.25 * tx + 0.75 * self.smooth_cx
                    self.smooth_cy = 0.25 * ty + 0.75 * self.smooth_cy
                    if zoom_action == "in":
                        self.sim_zoom = min(2.4, self.sim_zoom + 0.03)
                else:
                    self.smooth_cx = 0.1 * 160.0 + 0.9 * self.smooth_cx
                    self.smooth_cy = 0.1 * 90.0 + 0.9 * self.smooth_cy
                    if locked_id is None:
                        self.sim_zoom = max(1.0, self.sim_zoom - 0.04)
                sim_zoom = self.sim_zoom
                scx, scy = self.smooth_cx, self.smooth_cy

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            vis = frame.copy()
            sx, sy = fw_full / 320.0, fh_full / 180.0

            for tr in group.get("tracks") or []:
                tid = tr["track_id"]
                bx, by, bw, bh = tr["box"]
                x1, y1, x2, y2 = int(bx * sx), int(by * sy), int((bx + bw) * sx), int((by + bh) * sy)
                is_locked = (tid == locked_id)
                is_spk = tr["speaking"]
                color = (0, 140, 255) if is_locked else ((0, 220, 0) if is_spk else (180, 180, 180))
                cv2.rectangle(vis, (x1, y1), (x2, y2), color, 3 if is_locked else 2)
                for pt in (tr.get("mouth_points") or []):
                    cv2.circle(vis, (int(pt[0] * sx), int(pt[1] * sy)), 2, (0, 255, 255) if is_spk else (255, 200, 0), -1)
                tag = f"ID {tid} | P={tr['speech_prob']:.2f} MAR={tr['inner_mar']:.2f}"
                if is_locked:
                    tag = f"LOCKED | {tag}"
                elif is_spk:
                    tag = f"SPEAKING | {tag}"
                cv2.putText(vis, tag, (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2)

            crop_w = max(64, int(fw_full / sim_zoom))
            crop_h = max(36, int(fh_full / sim_zoom))
            ccx, ccy = int(scx * sx), int(scy * sy)
            cx1 = max(0, min(fw_full - crop_w, ccx - crop_w // 2))
            cy1 = max(0, min(fh_full - crop_h, ccy - crop_h // 2))
            portrait = cv2.resize(frame[cy1:cy1 + crop_h, cx1:cx1 + crop_w], (640, 360))
            cv2.putText(
                portrait,
                f"Auto-Zoom {sim_zoom:.2f}x | {group.get('reason', 'idle')}",
                (14, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 255),
                2,
            )

            _, g_enc = cv2.imencode(".jpg", cv2.resize(vis, (800, 450)), [cv2.IMWRITE_JPEG_QUALITY, 82])
            _, p_enc = cv2.imencode(".jpg", portrait, [cv2.IMWRITE_JPEG_QUALITY, 85])
            with self.lock:
                self.group_jpg = g_enc.tobytes()
                self.portrait_jpg = p_enc.tobytes()
                self.telemetry = {
                    "model": group.get("landmarks_model", "flux_fallback"),
                    "ms": elapsed_ms,
                    "zoom": sim_zoom,
                    "reason": group.get("reason", "idle"),
                    "locked_id": locked_id,
                    "tracks": group.get("tracks") or [],
                }
            time.sleep(0.015)

    def stop(self) -> None:
        self.stop_event.set()
        self.cap.release()


def main() -> int:
    runner = WebcamLipSyncRunner(0)
    app = FastAPI()

    @app.get("/")
    def index():
        return HTMLResponse(HTML_PAGE)

    @app.post("/reset")
    def reset():
        runner.reset()
        return {"ok": True}

    @app.get("/telemetry")
    def telemetry():
        with runner.lock:
            return JSONResponse(runner.telemetry)

    def mjpeg_gen(kind: str):
        while not runner.stop_event.is_set():
            with runner.lock:
                jpg = runner.group_jpg if kind == "group" else runner.portrait_jpg
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.04)

    @app.get("/stream/{kind}")
    def stream(kind: str):
        return StreamingResponse(mjpeg_gen(kind), media_type="multipart/x-mixed-replace; boundary=frame")

    url = "http://127.0.0.1:4567"
    print(f"Webcam Lip-Sync Test live at {url}", flush=True)
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=4567, log_level="warning")
    finally:
        runner.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
