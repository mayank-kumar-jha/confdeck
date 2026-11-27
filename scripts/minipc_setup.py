import sqlite3
import json
import shutil
from pathlib import Path

root = Path("/home/ordain/.local/share/ConfDeck")
root.mkdir(parents=True, exist_ok=True)

# 1. Copy room-settings backup to active backup
backup_src = Path("/home/ordain/sehnizer/room-settings.backup.json")
backup_dst = root / "settings.backup.json"
shutil.copy2(backup_src, backup_dst)
print("Copied room-settings backup to settings.backup.json")

# 2. Re-create settings.db with the exact verified settings
db_path = root / "settings.db"
if db_path.exists():
    db_path.unlink()

db = sqlite3.connect(db_path)
db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
db.execute("CREATE TABLE IF NOT EXISTS displays (id TEXT PRIMARY KEY, name TEXT, expires REAL)")
db.execute("CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, message TEXT NOT NULL)")
db.execute("PRAGMA user_version=2")

data = backup_src.read_text()
db.execute("INSERT OR REPLACE INTO settings VALUES (1, ?)", (data,))
db.commit()
db.close()
print("Settings successfully written to settings.db!")

# 3. Write secrets.bin with the exact passwords
secrets = {
    "mic": {"password": "Admin@12345"},
    "de6f408a7b0c": {"username": "admin", "password": "admin"},
    "604f8cd3d328": {"username": "admin", "password": "admin123"},
    "a736bacef244": {"username": "", "password": ""},
    "06955d4010ee": {"username": "admin", "password": "admin123"},
    "61c1e1f2537d": {"username": "admin", "password": "admin123"},
    "2994f040751d": {"username": "admin", "password": "admin123"}
}
(root / "secrets.bin").write_text(json.dumps(secrets))
(root / "secrets.bin").chmod(0o600)
print("Secrets written to secrets.bin!")

# Remove lock
(root / "instance.lock").unlink(missing_ok=True)
print("Done!")

