import paramiko
import time
import os

MINI_PC_IP = '192.168.1.84'
USER = 'ordain'
PASSWORD = '0208'

print(f"Connecting to Mini PC at {MINI_PC_IP}...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(MINI_PC_IP, username=USER, password=PASSWORD)
sftp = ssh.open_sftp()

def run_sudo(cmd):
    stdin, stdout, stderr = ssh.exec_command(f'echo {PASSWORD} | sudo -S {cmd}')
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err and "password" not in err.lower() and "[sudo]" not in err:
        print(f"[ERR] {cmd}: {err}")
    return out

# 1. Upload updated files
print("Uploading updated app files...")
local_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

files_to_upload = [
    ("confdeck/app.py", "/home/ordain/sehnizer/confdeck/app.py"),
    ("confdeck/runtime.py", "/home/ordain/sehnizer/confdeck/runtime.py"),
    ("confdeck/static/app.js", "/home/ordain/sehnizer/confdeck/static/app.js"),
]

for rel_src, remote_dst in files_to_upload:
    local_path = os.path.join(local_root, rel_src.replace('/', os.sep))
    print(f"  Uploading {rel_src} -> {remote_dst}")
    sftp.put(local_path, remote_dst)

# 2. Stop any existing manually started processes
print("Stopping any manual background confdeck / mediamtx processes...")
run_sudo("pkill -9 -f confdeck || true")
run_sudo("pkill -9 -f mediamtx || true")
run_sudo("rm -f /home/ordain/.local/share/ConfDeck/instance.lock")
time.sleep(1)

# 3. Create sudoers file for passwordless systemctl and reboot
print("Configuring sudoers permissions for ordain...")
sudoers_content = "ordain ALL=(ALL) NOPASSWD: /bin/systemctl restart confdeck, /bin/systemctl stop confdeck, /bin/systemctl start confdeck, /bin/systemctl status confdeck, /bin/systemctl is-active confdeck, /usr/sbin/reboot, /usr/sbin/shutdown\n"
with sftp.open('/tmp/confdeck_sudoers', 'w') as f:
    f.write(sudoers_content)
run_sudo("cp /tmp/confdeck_sudoers /etc/sudoers.d/confdeck")
run_sudo("chmod 0440 /etc/sudoers.d/confdeck")
run_sudo("rm -f /tmp/confdeck_sudoers")

# 4. Create sysctl config for localnet routing
print("Configuring sysctl persistence for localnet routing...")
sysctl_content = "net.ipv4.conf.all.route_localnet=1\n"
with sftp.open('/tmp/99-confdeck.conf', 'w') as f:
    f.write(sysctl_content)
run_sudo("cp /tmp/99-confdeck.conf /etc/sysctl.d/99-confdeck.conf")
run_sudo("sysctl --system")
run_sudo("rm -f /tmp/99-confdeck.conf")

# 5. Create iptables systemd service to persist port 80 -> 8765 forwarding across reboots
print("Creating confdeck-iptables.service...")
iptables_service = """[Unit]
Description=ConfDeck Port 80 to 8765 Forwarding
Before=confdeck.service
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8765 2>/dev/null || iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8765; sysctl -w net.ipv4.conf.all.route_localnet=1'

[Install]
WantedBy=multi-user.target
"""
with sftp.open('/tmp/confdeck-iptables.service', 'w') as f:
    f.write(iptables_service)
run_sudo("cp /tmp/confdeck-iptables.service /etc/systemd/system/confdeck-iptables.service")
run_sudo("rm -f /tmp/confdeck-iptables.service")

# 6. Create ConfDeck systemd service
print("Creating confdeck.service...")
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
ExecStartPre=-/usr/bin/pkill -9 -f mediamtx
ExecStart=/home/ordain/sehnizer/confdeck.sh start --no-browser --display-host 192.168.1.84
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""
with sftp.open('/tmp/confdeck.service', 'w') as f:
    f.write(confdeck_service)
run_sudo("cp /tmp/confdeck.service /etc/systemd/system/confdeck.service")
run_sudo("rm -f /tmp/confdeck.service")

# 7. Reload systemd, enable services, and start
print("Reloading systemd daemon, enabling and starting services...")
run_sudo("systemctl daemon-reload")
run_sudo("systemctl enable confdeck-iptables.service")
run_sudo("systemctl start confdeck-iptables.service")
run_sudo("systemctl enable confdeck.service")
run_sudo("systemctl restart confdeck.service")

print("Waiting 6 seconds for system startup and probes...")
time.sleep(6)

# 8. Check service status
print("=== confdeck.service Status ===")
out = run_sudo("systemctl status confdeck.service --no-pager")
print(out)

sftp.close()
ssh.close()
print("Deployment completed!")

