import paramiko, json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.1.84', username='ordain', password='0208')

def run_cmd(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(f"=== {cmd} ===")
    if out: print(out)
    if err: print("[ERR]", err)
    return out

run_cmd("""python3 -c "import sqlite3, json
db = sqlite3.connect('/home/ordain/.local/share/ConfDeck/settings.db')
row = db.execute('select body from settings where id=1').fetchone()
if row:
    body = json.loads(row[0])
    for c in body.get('cameras', []):
        print(c['id'], c['ip'], c['path'], c.get('control_protocol'))
" """)

run_cmd("cat /home/ordain/.local/share/ConfDeck/secrets.bin")
ssh.close()

