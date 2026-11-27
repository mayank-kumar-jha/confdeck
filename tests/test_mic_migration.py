"""Saved microphone selection migration and its one-time operator notice."""

import json
import sqlite3

import pytest

from confdeck.models import MicSettings, Settings
from confdeck.store import Store


@pytest.mark.parametrize("selection", [
    {"protocol": "ssc"}, {"model": "ceiling_2"}, {"model": "tcc2"},
    {"model": "TeamConnect Ceiling 2"},
])
@pytest.mark.parametrize("source", ["database", "backup"])
def test_legacy_mic_migration_preserves_ip_and_notice_until_dismissed(tmp_path, selection, source):
    data = Settings(room_name="Existing room").model_dump()
    data["mic"].update(selection, ip="192.168.1.77", port=45, azimuth_offset=17)
    body = json.dumps(data)
    if source == "database":
        with sqlite3.connect(tmp_path / "settings.db") as db:
            db.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("INSERT INTO settings VALUES (1, ?)", (body,))
            db.execute("PRAGMA user_version=1")
    else:
        (tmp_path / "settings.backup.json").write_text(body)
    store = Store(tmp_path)
    settings = store.load()
    assert settings.room_name == "Existing room"
    assert settings.mic.ip == "192.168.1.77"
    assert settings.mic.port == 443
    assert settings.mic.azimuth_offset == 17
    assert "password" in store.mic_notice()
    saved = json.loads(store.db.execute("SELECT body FROM settings").fetchone()[0])
    assert saved == json.loads(store.backup.read_text())
    assert saved["mic"]["protocol"] == "sscv2" and "model" not in saved["mic"]
    store.close()

    store = Store(tmp_path)
    assert store.load() == settings
    assert store.mic_notice()
    store.dismiss_mic_notice()
    store.close()
    store = Store(tmp_path)
    assert store.load() == settings
    assert not store.mic_notice()
    store.close()


def test_saved_medium_connection_and_mapping_unchanged(tmp_path):
    settings = Settings(mic=MicSettings(
        ip="192.168.1.88", port=8443, enabled=True, tls_fingerprint="ab" * 32,
        resource_paths=["/api/custom/direction"], field_map={"azimuth": "az", "elevation": "el"},
    ))
    data = settings.model_dump()
    data["mic"]["protocol"] = "sscv2"
    store = Store(tmp_path)
    with store.db:
        store.db.execute("INSERT INTO settings VALUES (1, ?)", (json.dumps(data),))
    assert store.load() == settings
    assert not store.mic_notice()
    store.close()


def test_tcc2_selection_survives_restart(tmp_path):
    settings = Settings(mic=MicSettings(ip="192.168.1.88", port=45,
                                        protocol="tcc2", enabled=True))
    store = Store(tmp_path)
    store.save(settings)
    store.close()
    store = Store(tmp_path)
    assert store.load() == settings
    assert not store.mic_notice()
    store.close()


@pytest.mark.parametrize("invalid", [[], None, {"mic": None}, {"mic": []}])
def test_invalid_saved_config_still_recovers_backup(tmp_path, invalid):
    store = Store(tmp_path)
    settings = Settings(room_name="Backup room")
    store.save(settings)
    with store.db:
        store.db.execute("UPDATE settings SET body=?", (json.dumps(invalid),))
    assert store.load() == settings
    assert store.warning
    store.close()


async def test_mic_notice_api_dismissal(runtime, client):
    data = runtime.settings.model_dump()
    data["mic"].update(protocol="ssc", port=45)
    runtime.settings = runtime.store._load_settings(json.dumps(data))
    state = (await client.get("/api/state")).json()
    assert state["mic_notice"]
    assert state["settings"]["mic"]["port"] == 443
    assert (await client.post("/api/mic/notice/dismiss", json={})).status_code == 200
    assert not (await client.get("/api/state")).json()["mic_notice"]
