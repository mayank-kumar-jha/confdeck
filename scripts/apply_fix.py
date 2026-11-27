import paramiko, json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

def run_cmd(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"=== {cmd} ===")
    if out: print(out)
    if err: print("[ERR]", err)
    return out

# 1. Stop old processes
run_cmd("pkill -9 -f confdeck || true")
run_cmd("pkill -9 -f mediamtx || true")

# 2. Apply verified settings to SQLite database
setup_script = '''
import sqlite3, json, shutil
from pathlib import Path

root = Path("/home/ordain/.local/share/ConfDeck")
root.mkdir(parents=True, exist_ok=True)

# Copy room-settings backup to active backup
backup_src = Path("/home/ordain/sehnizer/room-settings.backup.json")
backup_dst = root / "settings.backup.json"
shutil.copy2(backup_src, backup_dst)

# Update settings.db directly
db = sqlite3.connect(root / "settings.db")
db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
db.execute("CREATE TABLE IF NOT EXISTS displays (id TEXT PRIMARY KEY, name TEXT, expires REAL)")
db.execute("CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, message TEXT NOT NULL)")
db.execute("PRAGMA user_version=2")
data = backup_src.read_text()
db.execute("INSERT OR REPLACE INTO settings VALUES (1, ?)", (data,))
db.commit()
db.close()
print("Settings successfully written to settings.db and settings.backup.json")

# Write secrets.bin
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
print("Secrets written to secrets.bin")
'''

stdin, stdout, stderr = ssh.exec_command(f'python3 -c """{setup_script}"""')
print(stdout.read().decode())
print(stderr.read().decode())

run_cmd("chmod 600 /home/ordain/.local/share/ConfDeck/secrets.bin")
run_cmd("rm -f /home/ordain/.local/share/ConfDeck/instance.lock")

ssh.close()
print("DONE!")

