import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

stdin, stdout, stderr = ssh.exec_command("python3 -c \"import sqlite3, json; db = sqlite3.connect('/home/ordain/.local/share/ConfDeck/settings.db'); row = db.execute('select body from settings where id=1').fetchone(); d = json.loads(row[0]); print('DB zones:', len(d.get('tracking', {}).get('zones', []))); print('DB zones names:', [z.get('name') for z in d.get('tracking', {}).get('zones', [])])\"")
print(stdout.read().decode())

ssh.close()

