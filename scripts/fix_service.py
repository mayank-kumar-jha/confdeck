import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

def run_sudo(cmd):
    stdin, stdout, stderr = ssh.exec_command(f'echo 0208 | sudo -S {cmd}')
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err and "password" not in err.lower() and "[sudo]" not in err:
        print(f"[ERR] {cmd}: {err}")
    return out

print("1. Stopping confdeck.service...")
run_sudo("systemctl stop confdeck.service")

print("2. Killing any lingering root/ordain python/mediamtx processes...")
run_sudo("pkill -9 -f confdeck || true")
run_sudo("pkill -9 -f mediamtx || true")
run_sudo("rm -f /home/ordain/.local/share/ConfDeck/instance.lock /root/.local/share/ConfDeck/instance.lock")
time.sleep(1)

print("3. Updating confdeck.service unit with elevated ExecStartPre...")
confdeck_service = """[Unit]
Description=ConfDeck Camera Tracking Desk
After=network-online.target confdeck-iptables.service
Wants=network-online.target

[Service]
Type=simple
User=ordain
Group=ordain
WorkingDirectory=/home/ordain/sehnizer
Environment=HOME=/home/ordain
Environment=PYTHONUNBUFFERED=1
ExecStartPre=+/bin/sh -c 'fuser -k 8765/tcp 8766/tcp 8554/tcp 2>/dev/null || true; rm -f /home/ordain/.local/share/ConfDeck/instance.lock'
ExecStart=/home/ordain/sehnizer/confdeck.sh start --no-browser --display-host 192.168.1.84
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""
sftp = ssh.open_sftp()
with sftp.open('/tmp/confdeck.service', 'w') as f:
    f.write(confdeck_service)
sftp.close()

run_sudo("cp /tmp/confdeck.service /etc/systemd/system/confdeck.service")
run_sudo("rm -f /tmp/confdeck.service")
run_sudo("systemctl daemon-reload")

print("4. Starting confdeck.service...")
run_sudo("systemctl start confdeck.service")

print("5. Waiting 6 seconds for startup...")
time.sleep(6)

out = run_sudo("systemctl status confdeck.service --no-pager")
print("=== Status ===")
print(out)

ssh.close()
