import paramiko, time, json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

# Kill any running instance
ssh.exec_command('pkill -9 -f confdeck || true')
time.sleep(1)

# Start ConfDeck in background via nohup
ssh.exec_command('nohup /home/ordain/sehnizer/confdeck.sh start --no-browser --display-host 192.168.1.84 > /home/ordain/sehnizer/stdout.log 2>&1 &')
print("Started ConfDeck in background...")

# Wait 6 seconds for initial probes
for i in range(10):
    time.sleep(2)
    stdin, stdout, stderr = ssh.exec_command('python3 -c "import urllib.request, json; req=urllib.request.Request(\'http://127.0.0.1:8765/api/state\', headers={\'x-confdeck\':\'1\'}); print(json.dumps({c[\'name\']: c.get(\'state\') for c in json.loads(urllib.request.urlopen(req, timeout=3).read())[\'cameras\']}))"')
    res = stdout.read().decode().strip()
    if res:
        print(f"Cameras status (t={i*2}s):", res)
        if "Online" in res:
            break
    else:
        print(f"Waiting for startup... (t={i*2}s)")

# Check log
stdin, stdout, stderr = ssh.exec_command('tail -n 20 /home/ordain/sehnizer/stdout.log')
print("=== ConfDeck Output ===")
print(stdout.read().decode())

ssh.close()

