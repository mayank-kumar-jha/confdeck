import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

def run_sudo(cmd):
    stdin, stdout, stderr = ssh.exec_command(f'echo 0208 | sudo -S {cmd}')
    print(f"=== {cmd} ===")
    print(stdout.read().decode())
    err = stderr.read().decode()
    if err and "password" not in err.lower() and "[sudo]" not in err:
        print("[ERR]", err)

run_sudo("lsof -i :8765 -i :8766 -i :8554")
run_sudo("ps -fp 37072 || true")
ssh.close()

