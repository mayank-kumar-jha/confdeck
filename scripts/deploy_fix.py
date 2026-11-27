import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

# Stop existing processes
ssh.exec_command('pkill -9 -f confdeck || true')
ssh.exec_command('pkill -9 -f mediamtx || true')

# SFTP upload minipc_setup.py
sftp = ssh.open_sftp()
sftp.put('scripts/minipc_setup.py', '/home/ordain/sehnizer/minipc_setup.py')
sftp.close()

# Execute minipc_setup.py
stdin, stdout, stderr = ssh.exec_command('python3 /home/ordain/sehnizer/minipc_setup.py')
print("=== SETUP OUTPUT ===")
print(stdout.read().decode())
print(stderr.read().decode())

# Ensure cli.py listens on 0.0.0.0
update_cli = '''
path = "/home/ordain/sehnizer/confdeck/cli.py"
with open(path, "r") as f:
    code = f.read()
code = code.replace("host=\"127.0.0.1\",", "host=\"0.0.0.0\",")
code = code.replace("free_port(\"127.0.0.1\", args.port)", "free_port(\"0.0.0.0\", args.port)")
with open(path, "w") as f:
    f.write(code)
print("cli.py updated")
'''
stdin, stdout, stderr = ssh.exec_command(f'python3 -c "{update_cli}"')
print(stdout.read().decode())

# Ensure iptables redirect on port 80
ssh.exec_command('echo 0208 | sudo -S iptables -t nat -F PREROUTING')
ssh.exec_command('echo 0208 | sudo -S iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8765')
ssh.exec_command('echo 0208 | sudo -S sysctl -w net.ipv4.conf.all.route_localnet=1')

# Now launch ConfDeck in the background on the Mini PC!
# We can use nohup or systemd, or let the user run ./confdeck.sh start
print("Database and credentials restored to the working 6-camera configuration!")
ssh.close()

