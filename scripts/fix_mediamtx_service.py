import paramiko, time, json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

def run_sudo(cmd):
    stdin, stdout, stderr = ssh.exec_command(f'echo 0208 | sudo -S {cmd}')
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"=== sudo {cmd} ===")
    if out: print(out)
    return out

# 1. Stop and disable the conflicting systemd mediamtx service
run_sudo("systemctl stop mediamtx")
run_sudo("systemctl disable mediamtx")
run_sudo("pkill -9 -f /home/ordain/mediamtx/mediamtx || true")
run_sudo("pkill -9 -f confdeck || true")
time.sleep(2)

# Check port 8554 is free now!
stdin, stdout, stderr = ssh.exec_command("ss -tulpn | grep 8554 || echo 'PORT 8554 IS COMPLETELY FREE!'")
print(stdout.read().decode())

# Check port 8889 is free now!
stdin, stdout, stderr = ssh.exec_command("ss -tulpn | grep 8889 || echo 'PORT 8889 IS COMPLETELY FREE!'")
print(stdout.read().decode())

# 2. Restart ConfDeck now that the port is free!
ssh.exec_command("nohup /home/ordain/sehnizer/confdeck.sh start --no-browser --display-host 192.168.1.84 > /home/ordain/sehnizer/stdout.log 2>&1 &")
print("ConfDeck restarted with full ownership of media ports!")

# 3. Wait and verify cameras
for i in range(12):
    time.sleep(2)
    stdin, stdout, stderr = ssh.exec_command("python3 -c \"import urllib.request, json; req=urllib.request.Request('http://127.0.0.1:8765/api/state', headers={'x-confdeck':'1'}); data=json.loads(urllib.request.urlopen(req, timeout=3).read()); print(json.dumps({c['name']: c.get('state') for c in data['cameras']}))\"")
    res = stdout.read().decode().strip()
    if res:
        print(f"Cameras (t={i*2}s):", res)
        # Check if all 6 are Online
        if "Waiting for relay" not in res and "Online" in res:
            states = json.loads(res)
            online_count = sum(1 for s in states.values() if s == "Online")
            if online_count == 6:
                print(">>> ALL 6 CAMERAS ARE NOW ONLINE! <<<")
                break
    else:
        print(f"Waiting for startup... (t={i*2}s)")

ssh.close()

