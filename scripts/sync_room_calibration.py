import paramiko
from pathlib import Path

backup_path = Path("C:/Users/saket/AppData/Local/ConfDeck/settings.backup.json")
laptop_settings = backup_path.read_text(encoding="utf-8")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

# 1. Write settings.backup.json
sftp = ssh.open_sftp()
with sftp.file('/home/ordain/.local/share/ConfDeck/settings.backup.json', 'w') as f:
    f.write(laptop_settings)

# Also write update script
updater_code = """
import sqlite3
from pathlib import Path
root = Path("/home/ordain/.local/share/ConfDeck")
data = (root / "settings.backup.json").read_text()
db = sqlite3.connect(root / "settings.db")
db.execute("INSERT OR REPLACE INTO settings (id, body) VALUES (1, ?)", (data,))
db.commit()
db.close()
print("SUCCESS: Updated settings.db with full room scenes and seats!")
"""
with sftp.file('/tmp/update_db.py', 'w') as f:
    f.write(updater_code)
sftp.close()

# 2. Run update script
stdin, stdout, stderr = ssh.exec_command('python3 /tmp/update_db.py')
print(stdout.read().decode())
err = stderr.read().decode()
if err: print("[ERR]", err)

# 3. Clean up
ssh.exec_command('rm -f /tmp/update_db.py')

# 4. Restart confdeck.service via sudoers
stdin, stdout, stderr = ssh.exec_command('sudo systemctl restart confdeck.service')
print("Restarting confdeck.service...")
print(stdout.read().decode())
err = stderr.read().decode()
if err and "password" not in err.lower(): print("[ERR]", err)

ssh.close()
