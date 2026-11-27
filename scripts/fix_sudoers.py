import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

sudoers_content = "ordain ALL=(ALL) NOPASSWD: /usr/bin/systemctl *, /bin/systemctl *, /usr/sbin/reboot, /usr/sbin/shutdown\n"
sftp = ssh.open_sftp()
with sftp.file('/tmp/confdeck_sudoers', 'w') as f:
    f.write(sudoers_content)
sftp.close()

stdin, stdout, stderr = ssh.exec_command('echo 0208 | sudo -S cp /tmp/confdeck_sudoers /etc/sudoers.d/confdeck && echo 0208 | sudo -S chmod 0440 /etc/sudoers.d/confdeck && rm -f /tmp/confdeck_sudoers')
print(stdout.read().decode())
err = stderr.read().decode()
if err and "password" not in err.lower(): print("[ERR]", err)

# Test NOPASSWD execution
stdin, stdout, stderr = ssh.exec_command('sudo systemctl restart confdeck.service')
print("NOPASSWD test stdout:", stdout.read().decode())
err = stderr.read().decode()
print("NOPASSWD test stderr:", err)

ssh.close()

