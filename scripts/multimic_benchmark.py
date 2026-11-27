"""Synthetic scaling and polling timing; never connects to room hardware."""
import asyncio
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from confdeck.evaluation.replay import make_engine
from confdeck.microphone import PositionParser
from confdeck.models import MicSettings
from confdeck.runtime import Runtime
from confdeck.tracking import TrackingSettings, Zone


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))]


def inference(kind):
    sources = ["primary", *[f"mic{i}" for i in range(1, 8)]]
    cfg = TrackingSettings(engine=kind, confirm_ms=300, min_hold_ms=500, settle_ms=0,
                           max_moves_per_min=60, level_gate=True)
    cfg.zones = [Zone(id=f"seat{i}", name=f"Seat {i}", mic_id=source,
                      azimuth=45, elevation=45, presets={f"cam{i}": "1"})
                 for i, source in enumerate(sources)]
    tracker = make_engine(cfg, kind)
    timings, decisions = [], []
    for frame in range(800):
        now = frame/10
        active = frame//100
        for i, source in enumerate(sources):
            event = {"mic_id": source, "azimuth": 45+math.sin(frame)*.2,
                     "elevation": 45, "level": -30 if i == active else -85,
                     "activity": i == active, "sample_time": now}
            start = time.perf_counter()
            decision = tracker.feed(event, now)
            timings.append((time.perf_counter()-start)*1000)
            if decision:
                decisions.append(decision)
        tracker.tick(now)
    assert [d["zone"] for d in decisions] == [f"seat{i}" for i in range(8)]
    return {"events": len(timings), "microphones": 8, "correct_transitions": 8,
            "unexpected_transitions": 0,
            "processing_ms_median": statistics.median(timings),
            "processing_ms_p95": percentile(timings, .95),
            "synthetic_onset_to_decision_ms": [(d["time"]-i*10)*1000 for i, d in enumerate(decisions)]}


async def polling():
    with tempfile.TemporaryDirectory(prefix="confdeck-benchmark-") as folder:
        runtime = Runtime(Path(folder))
        paths = runtime.device_settings.config["telemetry"]

        async def request(method, path):
            await asyncio.sleep(.04)
            return {"peak": -30} if path == paths["level"] else {"azimuth": 45, "elevation": 45}

        mic = SimpleNamespace(parser=PositionParser(MicSettings()), resource_request=request)
        runtime.microphones["primary"] = mic
        serial, parallel = [], []
        try:
            for _ in range(12):
                start = time.perf_counter()
                await request("GET", paths["level"])
                await request("GET", paths["direction"])
                serial.append((time.perf_counter()-start)*1000)
                start = time.perf_counter()
                await runtime.poll_microphone_once("primary", mic)
                parallel.append((time.perf_counter()-start)*1000)
            return {"injected_request_ms": 40,
                    "previous_serial_median_ms": statistics.median(serial),
                    "current_parallel_median_ms": statistics.median(parallel),
                    "current_parallel_p95_ms": percentile(parallel, .95)}
        finally:
            runtime.microphones.clear()
            await runtime.close()


async def main():
    report = {"provenance": "Synthetic events and mocked requests; not room accuracy or physical latency",
              "engines": {kind: inference(kind) for kind in ("legacy", "new")},
              "polling": await polling()}
    out = Path("docs/reports/multimic-benchmark.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
