import paramiko, time
from pathlib import Path

backup_path = Path("C:/Users/saket/AppData/Local/ConfDeck/settings.backup.json")
laptop_settings = backup_path.read_text(encoding="utf-8")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

# Stop confdeck
ssh.exec_command('pkill -9 -f confdeck || true')
time.sleep(1)

# Upload the exact json
sftp = ssh.open_sftp()
with sftp.file('/home/ordain/.local/share/ConfDeck/settings.backup.json', 'w') as f:
    f.write(laptop_settings)

# Write a python script to update sqlite cleanly
script = """
import sqlite3
from pathlib import Path
root = Path('/home/ordain/.local/share/ConfDeck')
data = (root / 'settings.backup.json').read_text()
db = sqlite3.connect(root / 'settings.db')
db.execute('INSERT OR REPLACE INTO settings VALUES (1, ?)', (data,))
db.commit()
db.close()
print('SUCCESS_SQLITE_UPDATED')
"""
with sftp.file('/home/ordain/sehnizer/update_db.py', 'w') as f:
    f.write(script)
sftp.close()

stdin, stdout, stderr = ssh.exec_command('python3 /home/ordain/sehnizer/update_db.py')
print(stdout.read().decode())
print(stderr.read().decode())

# Start confdeck
ssh.exec_command('nohup /home/ordain/sehnizer/confdeck.sh start --no-browser --display-host 192.168.1.84 > /home/ordain/sehnizer/stdout.log 2>&1 &')
print("Started ConfDeck!")

time.sleep(4)

# Query live state
import urllib.request, json
req = urllib.request.Request('http://192.168.1.84:8765/api/state', headers={'x-confdeck':'1'})
data = json.loads(urllib.request.urlopen(req, timeout=5).read())

print("=== FINAL LIVE STATE ON MINI PC ===")
print("Mic Status:", data.get("mic", {}).get("state"))
for c in data.get("cameras", []):
    print(f"  • {c['name']} ({c['ip']}): {c.get('state')} | {c.get('codec')} {c.get('width')}x{c.get('height')} @ {c.get('fps')}fps")
print("Tracking Seats:")
for z in data.get("tracking", {}).get("zones", []):
    print(f"  • Seat {z['name']} (az={z['azimuth']}, el={z['elevation']}) linked to {z.get('presets')}")

ssh.close()

