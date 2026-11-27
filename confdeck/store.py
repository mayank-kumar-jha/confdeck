"""Versioned SQLite settings with validated last-known-good recovery."""

import json
import sqlite3
import time
from pathlib import Path

from confdeck.models import Settings
from confdeck.security import atomic_write, protect


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        protect(root, directory=True)
        self.root = root
        self.warning = ""
        self.backup = root / "settings.backup.json"
        try:
            self.db = sqlite3.connect(root / "settings.db", check_same_thread=False)
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("corrupt")
        except sqlite3.DatabaseError:
            self.db.close()
            (root / "settings.db").rename(root / f"settings.corrupt-{time.time_ns()}.db")
            self.db = sqlite3.connect(root / "settings.db", check_same_thread=False)
            self.warning = (
                "Settings database was damaged. Restored the last valid backup; review Setup."
            )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self) -> None:
        """Apply transactional schema migrations, preserving existing settings."""
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise RuntimeError(
                "This settings file needs a newer ConfDeck. Use the matching bundle."
            )
        if version < 1:
            with self.db:
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)"
                )
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS displays (id TEXT PRIMARY KEY, name TEXT, expires REAL)"
                )
                self.db.execute("PRAGMA user_version=1")
        if version < 2:
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, message TEXT NOT NULL)")
                self.db.execute("PRAGMA user_version=2")

    def _load_settings(self, body: str) -> Settings:
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("mic", {}), dict):
            return Settings.model_validate(data)
        removed = "audio" in data or "captions" in data.get("display", {})
        data.pop("audio", None)
        data.get("display", {}).pop("captions", None)
        mic = data.get("mic", {})
        original = dict(mic)
        protocol = mic.get("protocol")
        model = str(mic.pop("model", "")).strip().lower().replace("_", " ")
        tcc2_model = model in {
            "ceiling 2", "tcc2", "teamconnect ceiling 2", "sennheiser teamconnect ceiling 2",
        }
        tcc2 = protocol == "tcc2" or tcc2_model or mic.get("port") == 45
        legacy = protocol == "ssc" and not tcc2
        if tcc2:
            mic["port"] = 45
            mic["protocol"] = "tcc2"
        elif legacy:
            mic["port"] = 443
            mic["protocol"] = "sscv2"
        settings = Settings.model_validate(data)
        if original != mic or removed:
            with self.db:
                if legacy:
                    self.db.execute(
                        "INSERT OR IGNORE INTO notices VALUES (?, ?)",
                        ("mic-password", "Microphone settings now use Sennheiser TeamConnect Ceiling Medium. "
                         "Enter its password from Control Cockpit in Microphone settings."),
                    )
                self.db.execute(
                    "INSERT OR REPLACE INTO settings VALUES (1, ?)",
                    (settings.model_dump_json(),),
                )
            atomic_write(self.backup, settings.model_dump_json().encode())
        return settings

    def mic_notice(self) -> str:
        row = self.db.execute("SELECT message FROM notices WHERE id='mic-password'").fetchone()
        return row[0] if row else ""

    def dismiss_mic_notice(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM notices WHERE id='mic-password'")

    def load(self) -> Settings:
        row = self.db.execute("SELECT body FROM settings WHERE id=1").fetchone()
        if row:
            try:
                return self._load_settings(row[0])
            except ValueError:
                self.warning = (
                    "Settings were invalid. Restored the last valid backup; review Setup."
                )
        if self.backup.exists():
            try:
                settings = self._load_settings(self.backup.read_text())
                self.save(settings)
                return settings
            except (ValueError, OSError):
                self.warning = "Settings and backup were unreadable. Setup defaults are active."
        return Settings()

    def save(self, settings: Settings) -> None:
        body = Settings.model_validate(settings.model_dump()).model_dump_json()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (1, ?)", (body,))
        atomic_write(self.backup, body.encode())

    def add_display(self, identity: str, name: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM displays WHERE expires < ?", (time.time(),))
            if self.db.execute("SELECT count(*) FROM displays").fetchone()[0] >= 32:
                self.db.execute(
                    "DELETE FROM displays WHERE id IN (SELECT id FROM displays ORDER BY expires LIMIT 1)"
                )
            self.db.execute(
                "INSERT OR REPLACE INTO displays VALUES (?, ?, ?)",
                (identity, name, time.time() + 30 * 86400),
            )

    def display_valid(self, identity: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM displays WHERE id=? AND expires>?", (identity, time.time())
            ).fetchone()
            is not None
        )

    def displays(self) -> list[dict]:
        return [
            {"id": row[0], "name": row[1]}
            for row in self.db.execute(
                "SELECT id,name FROM displays WHERE expires>?", (time.time(),)
            )
        ]

    def revoke(self, identity: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM displays WHERE id=?", (identity,))

    def close(self) -> None:
        self.db.close()
