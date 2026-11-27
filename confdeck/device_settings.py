"""Verified SSCv2 controls, with a persistent per-device pre-change snapshot."""

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path

import httpx
import yaml

log = logging.getLogger(__name__)
CONFIG = Path(__file__).parent / "protocols" / "tcc_medium.yaml"


def validate(value, schema, label="setting"):
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{label}: provide settings to change.")
        props = schema.get("properties", {})
        if set(value) - props.keys() or set(schema.get("required", [])) - value.keys():
            raise ValueError(f"{label}: unknown or missing fields.")
        for k, v in value.items():
            validate(v, props[k], f"{label}.{k}")
        return
    valid = {"boolean": isinstance(value, bool), "string": isinstance(value, str),
             "integer": type(value) is int,
             "number": type(value) in (int, float)}.get(kind, False)
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ValueError(f"{label}: choose a supported value.")
    if kind in ("integer", "number"):
        step = schema.get("multipleOf", 1 if kind == "integer" else None)
        if (not math.isfinite(value) or value < schema.get("minimum", -math.inf)
                or value > schema.get("maximum", math.inf)
                or (step and not math.isclose(value / step, round(value / step), abs_tol=1e-7))):
            raise ValueError(f"{label}: value is outside its supported range or step.")


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    return expected == actual


def writable(value, schema):
    if schema.get("type") == "object":
        return {k: writable(v, schema["properties"][k]) for k, v in value.items() if k in schema["properties"]}
    return value


class DeviceSettings:
    def __init__(self, runtime, source="primary"):
        self.runtime = runtime
        self.source = source
        override = runtime.root / "tcc_medium.yaml"
        self.config = yaml.safe_load((override if override.exists() else CONFIG).read_text(encoding="utf-8"))
        self.lock = asyncio.Lock()
        self.resources = {}
        for name, resource in self.config["resources"].items():
            for identity in resource.get("ids", [None]):
                key = f"{name}_{identity}" if identity is not None else name
                path = resource["path"].replace("{id}", str(identity))
                if not re.fullmatch(r"/api/[a-zA-Z0-9/_-]+", path):
                    raise ValueError("Device resource paths must stay under /api/.")
                self.resources[key] = {**resource, "path": path}
        runtime.store.db.execute("CREATE TABLE IF NOT EXISTS mic_snapshots (device TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)")
        runtime.store.db.commit()

    def device_key(self):
        s = self.runtime.settings.mic if self.source == "primary" else next(
            (m for m in self.runtime.settings.microphones if m.id == self.source), None)
        if s is None:
            raise ValueError("Microphone no longer configured.")
        return f"{s.ip}:{s.port}:{s.tls_fingerprint}"

    async def request(self, method, key, value=None):
        mic = self.runtime.mic if self.source == "primary" else self.runtime.microphones.get(self.source)
        if not mic or not hasattr(mic, "resource_request"):
            raise ValueError("Device settings need a connected SSCv2 Ceiling Medium microphone.")
        try:
            return await mic.resource_request(method, self.resources[key]["path"], **({"json": value} if value is not None else {}))
        except httpx.HTTPStatusError as exc:
            messages = {401: "Microphone password was rejected. Check the third-party API password.",
                        403: "The microphone denied this setting. Check API access permissions.",
                        404: "This setting is unavailable on the microphone firmware.",
                        400: "The microphone rejected the setting format.",
                        409: "The microphone rejected these zone boundaries.",
                        422: "The microphone rejected a value or zone width."}
            raise ValueError(messages.get(exc.response.status_code, "The microphone could not finish this request.")) from exc
        except httpx.HTTPError as exc:
            raise ValueError("The microphone did not respond. Check its connection and retry.") from exc

    async def read(self):
        values, unavailable = {}, {}
        for key in self.resources:
            try:
                values[key] = await self.request("GET", key)
            except ValueError as exc:
                unavailable[key] = str(exc)
        row = self.runtime.store.db.execute("SELECT created FROM mic_snapshots WHERE device=?", (self.device_key(),)).fetchone()
        return {"values": values, "unavailable": unavailable, "resources": self.resources,
                "snapshot_at": row[0] if row else None, "factory_reset": False,
                "note": "Audio factory reset is not exposed in the published TCC Medium 1.9 API. Use Control Cockpit for a factory reset."}

    async def apply(self, changes, confirmed=False, revert=False):
        if not confirmed:
            raise ValueError("Review and confirm the device changes first.")
        async with self.lock:
            key = self.device_key()
            row = self.runtime.store.db.execute("SELECT body FROM mic_snapshots WHERE device=?", (key,)).fetchone()
            if revert:
                if not row:
                    raise ValueError("No snapshot exists for this microphone.")
                changes = json.loads(row[0])
            if not changes or set(changes) - self.resources.keys():
                raise ValueError("Choose supported microphone settings to change.")
            for name, value in changes.items():
                validate(value, self.resources[name]["schema"], name)
            # Read every exposed control before the first write. An incomplete snapshot
            # blocks writes, rather than silently losing the operator's original setup.
            if not row:
                current = await self.read()
                if current["unavailable"]:
                    raise ValueError("Snapshot incomplete. Resolve unavailable controls before changing device settings: " + ", ".join(current["unavailable"]))
                snapshot = {k: writable(v, self.resources[k]["schema"]) for k, v in current["values"].items()}
                with self.runtime.store.db:
                    self.runtime.store.db.execute("INSERT INTO mic_snapshots VALUES (?, ?, ?)", (key, json.dumps(snapshot), time.time()))
                log.info("Microphone settings snapshot saved before first change")
            completed = []
            try:
                for name, value in changes.items():
                    await self.request("PUT", name, value)
                    actual = await self.request("GET", name)
                    if not subset(value, actual):
                        raise ValueError(f"Read-back did not match for {name}. Review current values or revert your snapshot.")
                    completed.append(name)
                    log.info("Microphone %s verified: %s", "revert" if revert else "change", name)
            except ValueError as exc:
                log.warning("Microphone settings interrupted after %s: %s", completed, exc)
                raise ValueError(f"{exc} Verified changes before interruption: {', '.join(completed) or 'none'}. Your snapshot is retained.") from exc
            return await self.read()
