"""Own ConfDeck microphone adapters inside the Port 4000 process."""

import asyncio
import concurrent.futures
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _confdeck_source_root() -> Path:
    configured = os.environ.get("CONFDECK_SOURCE_ROOT", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate / "confdeck" / "runtime.py").is_file():
            return candidate
    raise RuntimeError("ConfDeck source was not found; set CONFDECK_SOURCE_ROOT.")


def _mic_data_root() -> Path:
    configured = os.environ.get("CONFDECK_MIC_DATA", "").strip()
    if configured:
        return Path(configured)
    if os.name != "nt":
        return Path("/home/ordain/.local/share/ConfDeck")
    return Path.home() / ".local" / "share" / "ConfDeck"


def _add_confdeck_venv_packages(source_root: Path) -> None:
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = (
        source_root / ".venv" / "lib" / python_version / "site-packages",
        source_root / ".venv" / "Lib" / "site-packages",
    )
    for candidate in candidates:
        if candidate.is_dir():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return


class Port4000MicService:
    """Run the existing device adapters on a private asyncio loop, not HTTP port 8765."""

    def __init__(self, root: Path | None = None, runtime_factory=None):
        self.root = Path(root) if root else _mic_data_root()
        self.runtime_factory = runtime_factory
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = None
        self._loop = None
        self._runtime = None
        self._startup_error = None

    def ensure_started(self, timeout=30):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="port4000-microphones",
                    daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("Port 4000 microphone runtime did not start in time.")
        if self._startup_error is not None:
            raise RuntimeError("Port 4000 microphone runtime failed to start.") from self._startup_error

    def _thread_main(self):
        try:
            source_root = _confdeck_source_root()
            source_path = str(source_root)
            if source_path not in sys.path:
                sys.path.insert(0, source_path)
            _add_confdeck_venv_packages(source_root)
            if self.runtime_factory is None:
                from confdeck.runtime import Runtime

                runtime_factory = Runtime
            else:
                runtime_factory = self.runtime_factory

            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._runtime = runtime_factory(self.root)
            self._loop.run_until_complete(self._start_runtime())
        except Exception as exc:
            self._startup_error = exc
            log.exception("Port 4000 microphone runtime failed to start")
            self._ready.set()
            return

        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _start_runtime(self):
        runtime = self._runtime
        changed = False
        for settings in [runtime.settings.mic, *runtime.settings.microphones]:
            if settings.port == 45 and settings.protocol != "tcc2":
                settings.protocol = "tcc2"
                changed = True
        if changed:
            runtime.save()

        runtime.follow.live = False
        runtime.background(runtime.poll_tracking_position())

    def _call(self, operation, timeout=30):
        self.ensure_started()
        if threading.current_thread() is self._thread:
            raise RuntimeError("Port 4000 microphone API cannot block its own event loop.")
        future = asyncio.run_coroutine_threadsafe(operation(), self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("Port 4000 microphone operation timed out.") from exc

    async def _snapshot(self):
        runtime = self._runtime
        mic = runtime.mic.status() if runtime.mic else {
            "state": "Not connected",
            "position": None,
            "raw": [],
            "history": [],
            "age": None,
            "rate": 0,
            "issue": None,
            "stale_fields": ["azimuth", "elevation"],
        }
        return {
            "settings": runtime.settings.model_dump(),
            "mic": mic,
            "microphones": [
                {
                    "id": source,
                    **{key: value for key, value in adapter.status().items()
                       if key not in ("raw", "history")},
                }
                for source, adapter in runtime.microphones.items()
            ],
            "mic_notice": runtime.store.mic_notice(),
            "tracking": runtime.follow.snapshot(),
            "far_end": runtime.far_end.snapshot(),
        }

    def snapshot(self):
        return self._call(self._snapshot)

    def _configured_microphones(self):
        runtime = self._runtime
        return {
            "primary": runtime.settings.mic,
            **{mic.id: mic for mic in runtime.settings.microphones},
        }

    async def _connect_microphones(self, sources):
        if not isinstance(sources, (list, tuple)):
            raise ValueError("Provide a list of microphones to connect.")
        source_ids = list(dict.fromkeys(sources))
        microphones = self._configured_microphones()
        async with self._runtime.write_lock:
            for source in source_ids:
                if not isinstance(source, str) or source not in microphones:
                    raise ValueError("Microphone not found.")
                settings = microphones[source]
                if not settings.enabled:
                    raise ValueError("Enable this microphone before connecting it.")
                if source == "primary":
                    await self._runtime.connect_mic(settings)
                else:
                    await self._runtime.connect_mic(settings, source)
        return {"ok": True, "connected": source_ids}

    def connect_microphones(self, sources):
        return self._call(lambda: self._connect_microphones(sources))

    async def _disconnect_microphones(self, sources):
        if not isinstance(sources, (list, tuple)):
            raise ValueError("Provide a list of microphones to disconnect.")
        source_ids = list(dict.fromkeys(sources))
        microphones = self._configured_microphones()
        async with self._runtime.write_lock:
            for source in source_ids:
                if not isinstance(source, str) or source not in microphones:
                    raise ValueError("Microphone not found.")
                settings = microphones[source]
                was_enabled = settings.enabled
                settings.enabled = False
                try:
                    if source == "primary":
                        await self._runtime.connect_mic(settings)
                    else:
                        await self._runtime.connect_mic(settings, source)
                finally:
                    settings.enabled = was_enabled
        return {"ok": True, "disconnected": source_ids}

    def disconnect_microphones(self, sources):
        return self._call(lambda: self._disconnect_microphones(sources))

    async def _tracking(self):
        return self._runtime.follow.snapshot()

    def tracking(self):
        return self._call(self._tracking)

    async def _save_microphone(self, body, source="primary", create=False):
        from confdeck.models import AdditionalMic, MicSettings, Settings

        runtime = self._runtime
        if not isinstance(body, dict) or not isinstance(body.get("settings"), dict):
            raise ValueError("Provide microphone settings.")
        if source != "primary" and not source.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid microphone selection.")

        settings_value = MicSettings.model_validate(body["settings"]).model_dump()
        password = body.get("password")
        if password is not None and not isinstance(password, str):
            raise ValueError("Microphone password must be a string.")

        async with runtime.write_lock:
            data = runtime.settings.model_dump()
            previous = runtime.settings.mic if source == "primary" else next(
                (mic for mic in runtime.settings.microphones if mic.id == source), None
            )
            if previous is None and not create:
                raise ValueError("Microphone not found.")
            if previous and (previous.ip, previous.port) != (
                settings_value["ip"], settings_value["port"]
            ):
                if settings_value["tls_fingerprint"] == previous.tls_fingerprint:
                    settings_value["tls_fingerprint"] = ""
                if password is None and settings_value["protocol"] == "sscv2":
                    raise ValueError("Enter the password when changing microphone address.")

            if source == "primary":
                data["mic"] = settings_value
            else:
                settings_value["id"] = source
                data["microphones"] = [
                    mic for mic in data["microphones"] if mic["id"] != source
                ] + [settings_value]
            candidate = Settings.model_validate(data)
            if password is not None:
                runtime.secrets.set(
                    "mic" if source == "primary" else f"mic:{source}",
                    password=password,
                )
            if source == "primary":
                runtime.settings.mic = candidate.mic
                settings = runtime.settings.mic
            else:
                settings = next(mic for mic in candidate.microphones if mic.id == source)
                runtime.settings.microphones = [
                    mic for mic in runtime.settings.microphones if mic.id != source
                ] + [settings]
            runtime.save()
            await runtime.connect_mic(settings, source)
        return {"ok": True, "id": source}

    def save_microphone(self, body, source="primary"):
        return self._call(lambda: self._save_microphone(body, source))

    def add_microphone(self, body):
        from confdeck.models import AdditionalMic

        source = AdditionalMic().id
        return self._call(lambda: self._save_microphone(body, source, create=True))

    async def _remove_microphone(self, source):
        runtime = self._runtime
        async with runtime.write_lock:
            mic = next((item for item in runtime.settings.microphones if item.id == source), None)
            if mic is None:
                raise ValueError("Select an additional microphone.")
            if any(
                zone.mic_id == source or source in zone.microphone_profiles
                for zone in runtime.settings.tracking.zones
            ):
                raise ValueError("Remove this microphone's seats first, or disconnect it to keep the seat map.")
            mic.enabled = False
            await runtime.connect_mic(mic, source)
            runtime.settings.microphones = [
                item for item in runtime.settings.microphones if item.id != source
            ]
            runtime.save()
            runtime.secrets.remove(f"mic:{source}")
        return {"ok": True}

    def remove_microphone(self, source):
        return self._call(lambda: self._remove_microphone(source))

    async def _save_tracking(self, body):
        from confdeck.tracking import TrackingSettings

        runtime = self._runtime
        settings = TrackingSettings.model_validate(body)
        async with runtime.write_lock:
            runtime.settings.tracking = settings
            runtime.follow.configure(settings)
            runtime.save()
        return {"ok": True}

    def save_tracking(self, body):
        return self._call(lambda: self._save_tracking(body))

    async def _set_tracking_mode(self, body):
        if bool(body.get("live")):
            raise ValueError("Camera preset control belongs to OmniTrack.")
        self._runtime.follow.mode(False)
        return {"ok": True, "live": False}

    def set_tracking_mode(self, body):
        return self._call(lambda: self._set_tracking_mode(body))

    def _device_settings_service(self, source):
        from confdeck.device_settings import DeviceSettings

        runtime = self._runtime
        if source == "primary":
            return runtime.device_settings
        if source not in {mic.id for mic in runtime.settings.microphones}:
            raise ValueError("Select a configured microphone.")
        return DeviceSettings(runtime, source)

    async def _read_device_settings(self, source):
        runtime = self._runtime
        async with runtime.mic_lock:
            return await self._device_settings_service(source).read()

    def read_device_settings(self, source="primary"):
        return self._call(lambda: self._read_device_settings(source), timeout=45)

    async def _write_device_settings(self, source, body, revert=False):
        runtime = self._runtime
        if not isinstance(body, dict):
            raise ValueError("Provide microphone device settings.")
        runtime.follow.stop("Microphone settings changed")
        async with runtime.mic_lock:
            return await self._device_settings_service(source).apply(
                body.get("changes") or {},
                confirmed=body.get("confirm") is True,
                revert=revert,
            )

    def write_device_settings(self, source, body, revert=False):
        return self._call(
            lambda: self._write_device_settings(source, body, revert),
            timeout=90,
        )

    async def _dismiss_mic_notice(self):
        self._runtime.store.dismiss_mic_notice()
        return {"ok": True}

    def dismiss_mic_notice(self):
        return self._call(self._dismiss_mic_notice)

    async def _confdeck_request(self, path, body, method):
        runtime = self._runtime
        if path == "state" and method == "GET":
            return await self._snapshot()
        if path == "tracking/mode" and method == "POST":
            return await self._set_tracking_mode(body)
        if path == "far-end" and method == "GET":
            from confdeck.far_end import inputs

            return {**runtime.far_end.snapshot(), "inputs": inputs()}
        if path == "far-end" and method == "PUT":
            await asyncio.to_thread(runtime.far_end.configure, body)
            return runtime.far_end.snapshot()
        if path == "far-end/test" and method == "POST":
            return await asyncio.to_thread(runtime.far_end.test_input, body)
        raise ValueError("This operation is not available in Port 4000 local mode.")

    def request(self, path, body=None, method=None, timeout=3):
        method = (method or ("POST" if body is not None else "GET")).upper()
        return self._call(
            lambda: self._confdeck_request(path, body, method),
            timeout=timeout,
        )

    def stop(self, timeout=10):
        if self._thread is None or self._loop is None or not self._thread.is_alive():
            return
        if self._runtime is not None:
            future = asyncio.run_coroutine_threadsafe(self._runtime.close(), self._loop)
            future.result(timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)


_SERVICE = Port4000MicService()


def get_port4000_mic_service() -> Port4000MicService:
    return _SERVICE
