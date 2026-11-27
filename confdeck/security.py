"""Protected local secrets, bounded authentication and redacted logging."""

import ctypes
import hashlib
import json
import logging
import logging.handlers
import os
import re
import secrets
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path

SECRET_VALUES: set[str] = set()


def redact(value):
    """Redact credentials recursively, including embedded RTSP user information."""
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if re.search(
                r"password|passwd|secret|token|authorization|cookie|pin|pairing_code", k, re.I
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"(rtsps?://)[^/\s]+@", r"\1[redacted]@", value)
    value = re.sub(
        r'(?i)(password|passwd|token|authorization|cookie|secret)([\s"\x27:=]+)[^\s,}\]]+',
        r"\1\2[redacted]",
        value,
    )
    for secret in SECRET_VALUES:
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def protect(path: Path, directory: bool = False) -> None:
    """Restrict persistent files to the current OS account; fail closed on errors."""
    if os.name == "nt":
        user = (
            subprocess.check_output(
                ["whoami", "/user", "/fo", "csv", "/nh"], creationflags=0x08000000, text=True
            )
            .strip()
            .split(",")[-1]
            .strip('"')
        )
        grant = f"*{user}:(OI)(CI)F" if directory else f"*{user}:F"
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            check=True,
            capture_output=True,
            creationflags=0x08000000,
        )
    else:
        path.chmod(0o700 if directory else 0o600)


def atomic_write(path: Path, data: bytes) -> None:
    """Write in a protected parent directory and atomically replace the destination."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if os.name != "nt":
        tmp.chmod(0o600)
    os.replace(tmp, path)


def dpapi(data: bytes, encrypt: bool) -> bytes:
    """Encrypt/decrypt using Windows DPAPI bound to the current login account."""

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    fn = (
        ctypes.windll.crypt32.CryptProtectData
        if encrypt
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Windows could not unlock the local secrets file.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


class SecretStore:
    """DPAPI on Windows, account-only permissions on Linux; no secrets in SQLite."""

    def __init__(self, root: Path):
        self.path = root / "secrets.bin"
        self.values = {}
        if self.path.exists():
            raw = self.path.read_bytes()
            self.values = json.loads(dpapi(raw, False) if os.name == "nt" else raw)
        for item in self.values.values():
            SECRET_VALUES.update(str(v) for v in item.values() if v)

    def get(self, key: str) -> dict:
        return self.values.get(key, {})

    def set(self, key: str, **values) -> None:
        self.values[key] = values
        SECRET_VALUES.update(str(v) for v in values.values() if v)
        self.save()

    def remove(self, key: str) -> None:
        self.values.pop(key, None)
        self.save()

    def save(self) -> None:
        raw = json.dumps(self.values).encode()
        atomic_write(self.path, dpapi(raw, True) if os.name == "nt" else raw)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "level": record.levelname,
                "component": record.name,
                "message": redact(record.getMessage()),
            }
        )


def setup_logs(root: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(
        root / "confdeck.log", maxBytes=2_000_000, backupCount=3, encoding="utf8"
    )
    handler.setFormatter(JsonFormatter())
    logging.getLogger("confdeck").setLevel(logging.INFO)
    logging.getLogger("confdeck").addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class Auth:
    """Startup admin PIN and time-limited TV pairing, with bounded attempt tracking."""

    def __init__(self, store):
        self.store = store
        self.pin = f"{secrets.randbelow(100_000_000):08}"
        self.admin_sessions: dict[str, float] = {}
        self.code = ""
        self.code_until = 0.0
        self.attempts = defaultdict(lambda: deque(maxlen=10))

    def permit_attempt(self, peer: str) -> bool:
        now = time.monotonic()
        if len(self.attempts) > 512:
            self.attempts.clear()
        queue = self.attempts[peer]
        while queue and now - queue[0] > 60:
            queue.popleft()
        if len(queue) >= 5:
            return False
        queue.append(now)
        return True

    def login(self, pin: str) -> str | None:
        if not secrets.compare_digest(pin, self.pin):
            return None
        token = secrets.token_urlsafe(32)
        self.admin_sessions[token] = time.time() + 86400
        if len(self.admin_sessions) > 32:
            self.admin_sessions.pop(next(iter(self.admin_sessions)))
        return token

    def admin(self, token: str) -> bool:
        return self.admin_sessions.get(token, 0) > time.time()

    def pairing(self) -> str:
        if time.time() >= self.code_until:
            self.code = f"{secrets.randbelow(1_000_000):06}"
            self.code_until = time.time() + 600
        return self.code

    def pair(self, code: str, name: str) -> str | None:
        if (
            not self.code
            or time.time() > self.code_until
            or not secrets.compare_digest(code, self.code)
        ):
            return None
        token = secrets.token_urlsafe(32)
        self.store.add_display(hashlib.sha256(token.encode()).hexdigest(), name[:80])
        return token

    def display(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        return digest if self.store.display_valid(digest) else None
