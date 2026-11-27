"""Independent component lifecycle and bounded live-state snapshots."""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from confdeck.device_settings import DeviceSettings
from confdeck.discovery import Scanner, networks
from confdeck.errors import problem
from confdeck.follow import Follow
from confdeck.media import Media
from confdeck.microphone import MockAdapter, SSCv2Adapter, TCC2Adapter
from confdeck.security import Auth, SecretStore, redact
from confdeck.store import Store

log = logging.getLogger("confdeck.runtime")


class Runtime:
    def __init__(
        self, root: Path, demo=False, display_host="127.0.0.1", admin_port=8765, display_port=8766
    ):
        self.root, self.demo = root, demo
        self.display_host, self.admin_port, self.display_port = (
            display_host,
            admin_port,
            display_port,
        )
        self.store = Store(root)
        self.settings = self.store.load()
        self.secrets = SecretStore(root)
        self.auth = Auth(self.store)
        simulated = {c.id for c in self.settings.cameras if c.demo}
        if simulated:
            self.settings.cameras = [c for c in self.settings.cameras if not c.demo]
            self.settings.display.cameras = [cid for cid in self.settings.display.cameras if cid not in simulated]
            self.settings.tracking.wide_presets = {
                cid: p for cid, p in self.settings.tracking.wide_presets.items() if cid not in simulated
            }
            for zone in self.settings.tracking.zones:
                zone.presets = {cid: p for cid, p in zone.presets.items() if cid not in simulated}
            self.store.save(self.settings)
        self.simulated_presets = {}
        self.mock_tracking_demo = False
        if demo:
            self.settings.wizard_done = True
        self.media = Media(root, self.settings, self.secrets, display_host)
        self.media.on_source_selected = self.save_camera_source
        self.scanner = Scanner(self.media, self.settings)
        self.mic = None
        self.microphones = {}
        self.mic_tasks = {}
        self.mock = None
        self.tasks = set()
        self.mic_event_task = None
        self.position_subscribers = set()
        self.display_seen = {}
        self.camera_metrics = {}
        from confdeck.camera_diagnostics import CameraDiagnostics
        self.diagnostics = CameraDiagnostics(self)
        self.media.on_failure = lambda camera: self.background(self.diagnostics.video(camera)) if camera.id not in self.diagnostics.reports else None
        self.health_data = {}
        self.started = time.monotonic()
        self.kiosks = []
        self.mic_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.preset_locks = {}
        self.whep_sessions = {}
        self.change_display_host = None
        self.request_shutdown = None
        from confdeck.far_end import FarEndMonitor
        self.far_end = FarEndMonitor(root)
        # Explicit opt-in. Missing/invalid audio inhibits new speech-assisted locks.
        self.speech_enabled = os.environ.get("CONFDECK_SPEECH_ENABLED") == "1"
        self.speech_capture = None
        self.speech_error = "Speech validation is off"
        if self.speech_enabled and (root / "speech-source-reset.json").exists():
            self.speech_error = "Audio source needs configuration after reset; new speech-assisted locks are paused"
        elif self.speech_enabled:
            try:
                from confdeck.speech import GateConfig
                from confdeck.speech_audio import Aes67Capture, AudioCapture
                model = os.environ.get("CONFDECK_SPEECH_MODEL", "")
                if not model:
                    raise ValueError("Set CONFDECK_SPEECH_MODEL")
                offset = float(os.environ.get("CONFDECK_SPEECH_OFFSET", "0"))
                config = GateConfig(low_voice=True, offset=offset)
                sdp = os.environ.get("CONFDECK_SPEECH_SDP", "")
                if sdp:
                    latency_ms = int(os.environ.get("CONFDECK_SPEECH_LATENCY_MS", "0"))
                    self.speech_capture = Aes67Capture(model, sdp, os.environ.get("CONFDECK_SPEECH_INTERFACE", "0.0.0.0"), latency_ms, config)
                else:
                    device = os.environ.get("CONFDECK_SPEECH_DEVICE", "")
                    self.speech_capture = AudioCapture(model, int(device) if device else None,
                                                       int(os.environ.get("CONFDECK_SPEECH_CHANNEL", "1")), config)
                self.speech_error = "Waiting for audio"
            except (TypeError, ValueError) as error:
                self.speech_error = f"Speech configuration unavailable: {error}"
        # Independent streams are opt-in and must be bound to verified sources.
        self.multi_audio = None
        self.multi_audio_error = ''
        self.multi_audio_enabled = bool(os.environ.get('CONFDECK_MULTI_AUDIO_CONFIG'))
        if self.multi_audio_enabled:
            try:
                from confdeck.seat_fusion.audio import IndependentAudio
                config_path = Path(os.environ['CONFDECK_MULTI_AUDIO_CONFIG'])
                config = json.loads(config_path.read_text(encoding='utf-8'))
                if not {'model', 'audio'}.issubset(config):
                    raise ValueError('Expected model and audio bindings')
                self.multi_audio = IndependentAudio(config['model'], config['audio'])
            except (OSError, TypeError, ValueError, KeyError) as error:
                self.multi_audio_error = f'Independent audio configuration unavailable: {error}'
        self.follow = Follow(self)
        from confdeck.manual import ManualControl
        self.manual = ManualControl(self)
        self.device_settings = DeviceSettings(self)
        self.relay_client = httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(12, read=30),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=24),
        )

    def background(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def done(t):
            self.tasks.discard(t)
            if not t.cancelled() and t.exception():
                log.warning("Background action failed: %s", type(t.exception()).__name__)

        task.add_done_callback(done)
        return task

    async def start(self):
        if self.multi_audio:
            try:
                self.multi_audio.start()
            except (OSError, RuntimeError, ValueError) as error:
                self.multi_audio_error = f'Independent audio start failed: {error}'
                self.multi_audio = None
        for camera in self.settings.cameras:
            if camera.handoff_mode == "live" or self.follow.handoffs.record(camera).get("test_pending"):
                try:
                    await self.follow.handoffs.disarm(camera, force=True)
                except (ValueError, OSError):
                    camera.control_enabled = False
                    self.follow.audit(f"Tracker OFF unverified on {camera.id}; control disabled")
        await self.media.start()
        self.media.demo_presets.update(self.simulated_presets)
        self.media.demo_tasks = [
            asyncio.create_task(self.media.demo_camera(camera))
            for camera in self.settings.cameras
            if self.demo and camera.demo
        ]
        if self.demo:
            self.settings.mic.enabled = True
            await self.connect_mic(self.settings.mic)
        elif self.settings.mic.enabled:
            await self.connect_mic(self.settings.mic)
        for settings in self.settings.microphones:
            await self.connect_mic(settings, settings.id)
        if self.mock_tracking_demo:
            for _ in range(30):
                if self.mic and self.mic.status().get("age") is not None:
                    break
                await asyncio.sleep(.1)
            self.follow.mode(True)
            self.follow.audit("Demo tracking active; camera preset changes are simulated")
        self.background(self.monitor())
        self.background(self.initial_probes())
        self.background(self.follow.run())
        self.background(self.poll_tracking_position())

    async def initial_probes(self):
        for _ in range(20):
            if self.media.state == "Connected":
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(2 if self.demo else 0)
        sem = asyncio.Semaphore(2)

        async def probe(camera):
            async with sem:
                await self.media.probe(camera)

        await asyncio.gather(*(probe(c) for c in self.settings.cameras))

    async def reboot(self):
        """Fully reboot cameras, microphone, media relays, and tracking seats from scratch."""
        log.info("Rebooting runtime: stopping tracking, mic, and media relay...")
        self.follow.stop("System reboot requested")

        # 1. Close microphones
        async with self.mic_lock:
            for source, tasks in list(self.mic_tasks.items()):
                for t in tasks:
                    t.cancel()
            self.mic_tasks.clear()
            for adapter in list(self.microphones.values()):
                try:
                    await adapter.close()
                except Exception:
                    pass
            self.microphones.clear()
            self.mic = None

        # 2. Stop media relay process
        try:
            if self.media.process and self.media.process.returncode is None:
                self.media.process.terminate()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # 3. Reload settings and secrets fresh from disk
        try:
            self.settings = self.store.load()
            self.secrets = SecretStore(self.root)
            self.media.settings = self.settings
            self.scanner.settings = self.settings
        except Exception as e:
            log.warning("Could not reload settings during reboot: %s", e)

        # 4. Restart media relay & trigger camera probes
        await self.media.start()
        self.background(self.initial_probes())

        # 5. Reconnect microphone(s)
        if self.settings.mic.enabled:
            await self.connect_mic(self.settings.mic)
        for s in self.settings.microphones:
            await self.connect_mic(s, s.id)

        # 6. Reconfigure and re-arm tracking engine & seats
        self.follow.configure(self.settings.tracking)
        log.info("Runtime reboot completed successfully.")

    def save(self):
        self.store.save(self.settings)
        self.media.settings = self.settings
        self.scanner.settings = self.settings

    def save_camera_source(self, camera, previous_path):
        current = next((c for c in self.settings.cameras if c.id == camera.id), None)
        if (current is not None and (current.ip, current.port) == (camera.ip, camera.port)
                and current.path in (previous_path, camera.path)
                and (current.transcode_h264, current.copy_h264) == (camera.transcode_h264, camera.copy_h264)):
            current.path = camera.path
            self.save()

    async def connect_mic(self, settings, source="primary"):
        if source == "primary" and self.speech_capture:
            if settings.enabled:
                self.speech_capture.start()
            else:
                await asyncio.to_thread(self.speech_capture.stop)
        async with self.mic_lock:
            self.follow.stop("Microphone connection changed")
            self.follow.configure(self.settings.tracking)
            old_tasks = self.mic_tasks.pop(source, [])
            for task in old_tasks:
                task.cancel()
            await asyncio.gather(*old_tasks, return_exceptions=True)
            old = self.microphones.pop(source, None)
            if old:
                await old.close()
            if source == "primary":
                self.mic = None
            if settings.enabled:
                if self.demo:
                    adapter = MockAdapter(settings)
                    if source == "primary":
                        self.mock = adapter
                elif settings.protocol == "tcc2" or settings.port == 45:
                    adapter = TCC2Adapter(settings)
                else:
                    def save_fingerprint(fp, _cert):
                        current = self.settings.mic if source == "primary" else next(
                            (m for m in self.settings.microphones if m.id == source), None)
                        if current and (current.ip, current.port) == (settings.ip, settings.port):
                            current.tls_fingerprint = fp
                            self.save()

                    adapter = SSCv2Adapter(
                        settings,
                        self.secrets.get("mic" if source == "primary" else f"mic:{source}").get("password", ""),
                        fingerprint_callback=save_fingerprint,
                    )
                self.microphones[source] = adapter
                if source == "primary":
                    self.mic = adapter
                    adapter.parser.on_raw = self.follow.lab.raw
                adapter.parser.on_sample = lambda event: self.follow.feed({**event, "mic_id": source})
                await adapter.connect()
                self.mic_tasks[source] = [
                    asyncio.create_task(self.forward_positions(adapter, source)),
                ]

    async def forward_positions(self, adapter=None, source="primary"):
        async for event in (adapter or self.mic).events():
            event = {**event, "mic_id": source}
            for queue in tuple(self.position_subscribers):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(event)

    async def poll_tracking_position(self):
        """SSCv2 SSE is change-only; fresh reads confirm a stationary speaker."""
        while True:
            for source, mic in tuple(self.microphones.items()):
                tasks = self.mic_tasks[source]
                if len(tasks) == 1:
                    tasks.append(asyncio.create_task(self.poll_microphone(source, mic)))
            await asyncio.sleep(.2)

    async def poll_microphone(self, source, mic):
        while self.microphones.get(source) is mic:
            started = time.monotonic()
            if isinstance(mic, SSCv2Adapter) and mic.state == "Connected":
                await self.poll_microphone_once(source, mic)
            await asyncio.sleep(max(.01, .2-(time.monotonic()-started)))

    async def poll_microphone_once(self, source, mic):
        paths = self.device_settings.config["telemetry"]
        started = time.monotonic()
        direction_revision = mic.parser.direction_revision
        level_revision = mic.parser.level_revision

        async def read(path):
            try:
                return await asyncio.wait_for(mic.resource_request("GET", path), timeout=.6)
            except (ValueError, httpx.HTTPError, TimeoutError):
                return None

        need_level = not getattr(mic, "level_subscription", False) or started-mic.parser.level_time >= .8
        requests = [read(paths["direction"])]
        if need_level:
            requests.append(read(paths["level"]))
        results = await asyncio.gather(*requests)
        if self.microphones.get(source) is not mic:
            return
        payload = {}
        # A newer SSE reading wins over an in-flight HTTP response.
        if results[0] is not None and mic.parser.direction_revision == direction_revision:
            payload[paths["direction"]] = results[0]
        if need_level and results[1] is not None and mic.parser.level_revision == level_revision:
            payload[paths["level"]] = results[1]
        if payload:
            mic.parser.consume(payload, time.monotonic())

    async def monitor(self):
        previous_wall, previous_mono = time.time(), time.monotonic()
        process = psutil.Process()
        while True:
            try:
                memory = psutil.virtual_memory()
                disk = shutil.disk_usage(self.root)
                now, mono = time.time(), time.monotonic()
                clock_skew = abs((now - previous_wall) - (mono - previous_mono)) > 5
                previous_wall, previous_mono = now, mono
                self.health_data = {
                    "cpu_percent": process.cpu_percent(),
                    "rss_mb": round(process.memory_info().rss / 1024**2, 1),
                    "available_ram_mb": round(memory.available / 1024**2),
                    "free_disk_mb": round(disk.free / 1024**2),
                    "uptime": mono - self.started,
                    "clock_skew": clock_skew,
                    "warning": self.store.warning,
                    "issues": [problem("disk")] if disk.free < 250 * 1024**2 else [],
                }
                await self.media.refresh()
                for camera in self.settings.cameras:
                    if camera.handoff_mode == "live":
                        online = self.media.statuses.get(camera.id, {}).get("state") == "Online"
                        await self.follow.handoffs.reconnect(camera, online)
                self.display_seen = {
                    k: v for k, v in self.display_seen.items() if now - v["last_seen"] < 86400
                }
            except Exception as exc:
                log.warning("Health check could not finish: %s", type(exc).__name__)
            await asyncio.sleep(2)

    def displays(self):
        now = time.time()
        return [
            {
                **d,
                **self.display_seen.get(d["id"], {}),
                "state": "Live"
                if now - self.display_seen.get(d["id"], {}).get("last_seen", 0) < 12
                else "Reconnecting",
            }
            for d in self.store.displays()
        ]

    def speech_status(self):
        if self.multi_audio_enabled and self.multi_audio:
            statuses = self.multi_audio.status() if hasattr(self.multi_audio, 'status') else {}
            healthy = bool(statuses and any(v != 'silent / waiting for speech' and 'error' not in v for v in statuses.values()))
            summary = ' · '.join(f"{k}: {v}" for k, v in statuses.items())
            return {"enabled": True, "healthy": healthy, "status": summary or "Independent audio active"}
        if not self.speech_enabled:
            return {"enabled": False, "healthy": False, "status": (
                "Single-microphone speech off; see independent audio" if self.multi_audio_enabled
                else "Off - level-only tracking")}
        if not self.speech_capture:
            return {"enabled": True, "healthy": False, "status": self.speech_error}
        tracking = self.settings.tracking
        if tracking.engine != "new" or any(zone.mic_id != "primary" or zone.microphone_profiles for zone in tracking.zones):
            return {"enabled": True, "healthy": False,
                    "status": "Speech mode requires the spherical engine and one primary microphone"}
        state = self.speech_capture.snapshot()
        return {"enabled": True, "healthy": state["frame"] is not None,
                "status": state["status"] if state["frame"] else "Audio unavailable or silent - new locks inhibited"}

    def speech_evidence(self, timestamp):
        capture = self.speech_capture
        if not capture or not capture.snapshot()["frame"]:
            return None
        now = time.monotonic()
        with capture.lock:
            frame = next((dict(f) for f in reversed(capture.frames) if f["time"] <= timestamp), None)
        if (not frame or frame.get("no_signal") or frame.get("clipping")
                or timestamp-frame["time"] > .096
                or now-(frame["time"]-(capture.config.offset if capture.config else 0)) > .096):
            return None
        return frame

    def snapshot(self):
        from confdeck.tracking import PROFILES
        mic = (
            self.mic.status()
            if self.mic
            else {
                "state": "Not connected",
                "position": None,
                "raw": [],
                "history": [],
                "age": None,
                "rate": 0,
                "issue": None,
            }
        )
        return {
            "demo": self.demo,
            "settings": self.settings.model_dump(),
            "cameras": [
                {
                    **c.model_dump(),
                    **self.media.statuses.get(c.id, {"state": "Waiting for camera", "issue": None}),
                    "metrics": self.camera_metrics.get(c.id),
                    "video": {"state": "OK" if self.camera_metrics.get(c.id, {}).get("frames_received") and time.time()-self.camera_metrics[c.id].get("at", 0) < 6 else self.media.statuses.get(c.id, {}).get("state", "Unverified")},
                    "control": self.diagnostics.controls.get(c.id, {"state": "Not checked"}),
                    "diagnosis": self.diagnostics.reports.get(c.id),
                    "relay_url": f"rtsp://{self.display_host}:8554/{c.id}",
                    **({"mock_preset": self.simulated_presets.get(c.id, "0")} if c.demo else {}),
                }
                for c in self.settings.cameras
            ],
            "mic": mic,
            "microphones": [{"id": source, **{k: v for k, v in adapter.status().items()
                                              if k not in ("raw", "history")}}
                            for source, adapter in self.microphones.items()],
            "mic_notice": self.store.mic_notice(),
            "tracking": self.follow.snapshot(),
            "speech": self.speech_status(),
            "independent_audio": {"enabled": self.multi_audio_enabled,
                                  "error": self.multi_audio_error,
                                  "sources": self.multi_audio.status() if self.multi_audio else {}},
            "far_end": self.far_end.snapshot(),
            "tracking_profiles": PROFILES,
            "accuracy": self.accuracy_status() if hasattr(self, "accuracy_status") else {},
            "media": {
                "state": self.media.state,
                "issue": self.media.issue,
                "restarts": self.media.restarts,
            },
            "displays": self.displays(),
            "health": self.health_data,
            "scan": {
                "state": self.scanner.state,
                "progress": self.scanner.progress,
                "results": self.scanner.results,
            },
            "network": networks(),
            "display_url": f"http://{self.display_host}:{self.display_port}/display",
            "lan_enabled": self.display_host != "127.0.0.1",
        }

    def display_state(self):
        ids = self.settings.display.cameras
        if not ids and not (self.follow.live and self.settings.tracking.conversation_layout):
            ids = [c.id for c in self.settings.cameras[:4]]
        return {
            "display": self.settings.display.model_dump(),
            "cameras": [{"id": c.id, "name": c.name} for c in self.settings.cameras if c.id in ids],
            "server_time": time.time(),
        }

    def diagnostics_snapshot(self):
        snapshot = self.snapshot()
        snapshot["mic"].pop("raw", None)
        snapshot["mic"].pop("history", None)
        return redact(snapshot)

    def monitors(self):
        try:
            from screeninfo import get_monitors

            return [
                {
                    "id": i,
                    "name": m.name or f"Display {i + 1}",
                    "x": m.x,
                    "y": m.y,
                    "width": m.width,
                    "height": m.height,
                    "primary": m.is_primary,
                }
                for i, m in enumerate(get_monitors())
            ]
        except Exception:
            return []

    async def open_monitor(self, index):
        monitors = await asyncio.to_thread(self.monitors)
        selected = next((m for m in monitors if m["id"] == index), None)
        if not selected:
            raise ValueError(
                "The selected monitor is no longer connected. Refresh the monitor list."
            )
        candidates = [
            shutil.which(n) for n in ("msedge", "google-chrome", "chromium", "chromium-browser")
        ]
        if os.name == "nt":
            candidates += [
                str(
                    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
                    / "Microsoft/Edge/Application/msedge.exe"
                ),
                str(
                    Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
                    / "Google/Chrome/Application/chrome.exe"
                ),
            ]
        executable = next((p for p in candidates if p and Path(p).exists()), None)
        if not executable:
            raise ValueError(
                "Install Edge, Chrome or Chromium from your offline installer, or open the display page manually."
            )
        url = f"http://{self.display_host}:{self.display_port}/display#code={self.auth.pairing()}"
        args = [
            executable,
            f"--app={url}",
            "--start-fullscreen",
            f"--window-position={selected['x']},{selected['y']}",
            f"--window-size={selected['width']},{selected['height']}",
            f"--user-data-dir={self.root / ('kiosk-' + str(index))}",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required",
        ]
        self.kiosks.append(
            subprocess.Popen(args, **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        )

    async def close(self):
        if self.multi_audio:
            await asyncio.to_thread(self.multi_audio.stop)
        await asyncio.to_thread(self.far_end.stop)
        if self.speech_capture:
            await asyncio.to_thread(self.speech_capture.stop)
        if getattr(self.follow.lab, "ab_task", None):
            self.follow.lab.ab_task.cancel()
            await asyncio.gather(self.follow.lab.ab_task, return_exceptions=True)
        await self.follow.lab.stop()
        self.follow.lab.reset_vision()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for tasks in self.mic_tasks.values():
            for task in tasks:
                task.cancel()
        await asyncio.gather(*(t for tasks in self.mic_tasks.values() for t in tasks), return_exceptions=True)
        await asyncio.gather(*(m.close() for key, m in self.microphones.items() if key != "primary"), return_exceptions=True)
        if self.mic_event_task:
            self.mic_event_task.cancel()
            await asyncio.gather(self.mic_event_task, return_exceptions=True)
        for camera in self.settings.cameras:
            if camera.id in self.follow.handoffs.armed:
                try:
                    await self.follow.handoffs.disarm(camera)
                except (ValueError, OSError):
                    self.follow.audit(f"Shutdown: could not confirm tracking OFF on {camera.id}")
        for driver in self.follow.handoffs.drivers.values():
            driver.close()
        await self.scanner.close()
        if self.mic:
            await self.mic.close()
        await self.media.close()
        await self.relay_client.aclose()
        for process in self.kiosks:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    with contextlib.suppress(psutil.NoSuchProcess):
                        child.terminate()
                parent.terminate()
                _, alive = await asyncio.to_thread(psutil.wait_procs, children + [parent], 3)
                for child in alive:
                    with contextlib.suppress(psutil.NoSuchProcess):
                        child.kill()
        self.store.close()
