"""Bounded gzip JSONL sessions; background writes keep the mic callback fast."""
import gzip
import json
import math
import queue
import threading
import uuid
from pathlib import Path


class Recorder:
    def __init__(self, root, tag="test", tags=(), max_bytes=2_000_000, keep=12, monotonic_start=None):
        if tag not in ("test", "calibration"):
            raise ValueError("Choose test or calibration")
        if not isinstance(tags, (list, tuple)) or len(tags) > 20 or any(not isinstance(t, str) or len(t) > 80 for t in tags):
            raise ValueError("Use up to twenty short session tags")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta = {"kind": "session", "version": 2, "id": uuid.uuid4().hex,
                     "tag": tag, "tags": list(tags)}
        if monotonic_start is not None:
            if type(monotonic_start) not in (int, float) or not math.isfinite(monotonic_start):
                raise ValueError("Invalid monotonic clock origin")
            self.meta["monotonic_start"] = monotonic_start
        self.max_bytes, self.keep = max_bytes, keep
        self.queue = queue.Queue(maxsize=2048)
        self.dropped = 0
        self.error = None
        self.closed = False
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()

    def put(self, kind, t, **data):
        if self.closed:
            return
        try:
            self.queue.put_nowait({"kind": kind, "t": t, **data})
        except queue.Full:
            self.dropped += 1

    def _write(self):
        part, size, stream = 0, 0, None
        try:
            while True:
                row = self.queue.get()
                if row is None:
                    break
                encoded = json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n"
                if stream is None or size + len(encoded) > self.max_bytes:
                    if stream:
                        stream.close()
                    path = self.root / f"{self.meta['id']}.{part:04d}.jsonl.gz"
                    stream = gzip.open(path, "wt", encoding="utf-8")
                    stream.write(json.dumps({**self.meta, "part": part}) + "\n")
                    part, size = part + 1, 0
                    for old in sorted(self.root.glob("*.jsonl.gz"), key=lambda p: p.stat().st_mtime)[:-self.keep]:
                        old.unlink()
                stream.write(encoded)
                size += len(encoded)
            if stream:
                stream.write(json.dumps({"kind": "health", "dropped": self.dropped}) + "\n")
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)
        finally:
            if stream:
                stream.close()

    def close(self):
        if not self.closed:
            self.closed = True
            if self.thread.is_alive() and not self.error:
                try:
                    self.queue.put(None, timeout=1)
                except queue.Full as exc:
                    raise ValueError("Recorder queue could not drain") from exc
            self.thread.join(timeout=10)
        if self.thread.is_alive() or self.error:
            raise ValueError(self.error or "Recorder did not finish")


def load(paths):
    meta, rows, total, expected_part = None, [], 0, 0
    for path in sorted(map(Path, paths)):
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                total += len(line)
                if total > 32_000_000:
                    raise ValueError("Session exceeds 32 MB decompressed")
                row = json.loads(line)
                if row.get("kind") == "session":
                    part = row.pop("part", expected_part)
                    if part != expected_part:
                        raise ValueError("Missing session chunk; load all parts before rotation")
                    expected_part += 1
                    if meta and row != meta:
                        raise ValueError("Do not combine different sessions")
                    meta = row
                else:
                    rows.append(row)
    if meta is None:
        raise ValueError("Missing session metadata")
    if any(r.get("dropped", 0) for r in rows):
        raise ValueError("Recording dropped events; repeat the trial")
    return {**meta, "rows": rows}


def validate(session, scoring=True):
    if session.get("tag") not in ("test", "calibration"):
        raise ValueError("Session must be explicitly tagged")
    if scoring and session["tag"] != "test":
        raise ValueError("Calibration sessions must never be scored")
    if session.get("incomplete"):
        raise ValueError("Session is incomplete; repeat the trial")
    previous = -1.0
    if len(session.get("rows", [])) > 100000:
        raise ValueError("Too many events")
    for row in session.get("rows", []):
        if "t" not in row:
            continue
        t = row["t"]
        if type(t) not in (float, int) or not math.isfinite(t) or t < previous or t > 36000:
            raise ValueError("Session times must be finite, ordered and within ten hours")
        previous = t
    return session
