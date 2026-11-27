"""Deploy the verified source delta to .31 with backup and rollback."""
import ast
import hashlib
import json
import shlex
import sys
import time
from pathlib import Path

import httpx
import paramiko

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / 'artifacts/mini31-candidate'
BASELINE = ROOT / 'artifacts/mini31-baseline'
FILES = [
    'sehnizer/confdeck/microphone.py',
    'sehnizer/confdeck/multimic.py',
    'sehnizer/confdeck/follow.py',
    'omnitrack-sennheiser/backend_services/driver_sennheiser.py',
    'omnitrack-sennheiser/backend_services/routes/sennheiser_routes.py',
    'omnitrack-sennheiser/public/js/modules/sennheiser_module.js',
    'omnitrack-sennheiser/public/js/setup.js',
    'omnitrack-sennheiser/public/css/premium.css',
    'omnitrack-sennheiser/public/html/setup.html',
]
PHASE2 = '--phase2' in sys.argv
PHASE3 = '--phase3' in sys.argv
PHASE4 = '--phase4' in sys.argv
PHASE6 = '--phase6' in sys.argv
PHASE7 = '--phase7' in sys.argv
PHASE8 = '--phase8' in sys.argv
if PHASE2:
    BASELINE = ROOT / 'artifacts/mini31-phase2-baseline'
    FILES = [
        'sehnizer/confdeck/multimic.py',
        'sehnizer/confdeck/follow.py',
        'omnitrack-sennheiser/public/js/modules/sennheiser_module.js',
    ]
if PHASE3:
    FILES = ['sehnizer/confdeck/media.py']
if PHASE4:
    FILES = ['omnitrack-sennheiser/backend_services/stream_manager.py']
if PHASE6:
    BASELINE = ROOT / 'artifacts/mini31-phase6-baseline'
    FILES = ['sehnizer/confdeck/multimic.py',
             'omnitrack-sennheiser/public/js/modules/sennheiser_module.js']
if PHASE7:
    BASELINE = ROOT / 'artifacts/mini31-phase7-baseline'
    FILES = ['sehnizer/confdeck/multimic.py',
             'omnitrack-sennheiser/public/js/modules/sennheiser_module.js']
if PHASE8:
    BASELINE = ROOT / 'artifacts/mini31-phase8-baseline'
    FILES = ['sehnizer/confdeck/multimic.py']
tree = ast.parse((ROOT / 'scripts/deploy_update_and_autostart.py').read_text())
creds = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
         if isinstance(n, ast.Assign) and len(n.targets) == 1
         and isinstance(n.targets[0], ast.Name) and n.targets[0].id in {'USER', 'PASSWORD'}}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(client, cmd):
    _, out, err = client.exec_command(cmd, timeout=60)
    output = out.read().decode(errors='replace')
    errors = err.read().decode(errors='replace')
    if out.channel.recv_exit_status():
        raise RuntimeError(errors or output or 'Remote command failed')
    return output


def settings_hash(state):
    return digest(json.dumps(state['settings']['tracking']['zones'], sort_keys=True).encode())


def main():
    http = httpx.Client(base_url='http://192.168.1.31:4000', timeout=8, trust_env=False)
    before = http.get('/api/sennheiser/state'); before.raise_for_status()
    original_state = before.json()
    original_hash = settings_hash(original_state)
    mode = http.get('/api/get_layout_mode'); mode.raise_for_status()
    original_mode = mode.json()
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect('192.168.1.31', username=creds['USER'], password=creds['PASSWORD'],
                   timeout=5, look_for_keys=False, allow_agent=False)
    sftp = client.open_sftp()
    modified = []
    paused = False
    backup = '/home/ordain/integration-backups/dualmic-' + time.strftime('%Y%m%d-%H%M%S')
    try:
        for rel in FILES:
            remote = '/home/ordain/' + rel
            with sftp.open(remote, 'rb') as source:
                live = source.read()
            if digest(live) != digest((BASELINE / rel).read_bytes()):
                raise RuntimeError('Installed source changed after review: ' + rel)
        run(client, 'mkdir -p ' + shlex.quote(backup))
        for rel in FILES:
            path = '/home/ordain/' + rel
            dest = backup + '/' + rel
            run(client, 'mkdir -p ' + shlex.quote(dest.rsplit('/', 1)[0]) + ' && cp -p ' + shlex.quote(path) + ' ' + shlex.quote(dest))
        if original_mode.get('mode') == 'auto':
            response = http.post('/api/set_layout_mode', json={'mode':'manual'}); response.raise_for_status()
            if not response.json().get('success'):
                raise RuntimeError('Could not pause automatic tracking')
            paused = True
        for rel in FILES:
            remote = '/home/ordain/' + rel
            temp = remote + '.dualmic-upload'
            attrs = sftp.stat(remote)
            sftp.put(str(CANDIDATE / rel), temp)
            sftp.chmod(temp, attrs.st_mode & 0o777)
            sftp.posix_rename(temp, remote)
            modified.append(rel)
        print('Source installed; restarting microphone and operator services.', flush=True)
        if PHASE4:
            run(client, 'sudo -n /usr/bin/systemctl restart stream_manager.service')
        else:
            run(client, 'sudo -n /usr/bin/systemctl restart confdeck.service')
        if not (PHASE2 or PHASE3 or PHASE4 or PHASE6 or PHASE7 or PHASE8):
            run(client, 'sudo -n /usr/bin/systemctl restart tracking_backend.service')
        recovered = None
        for _ in range(30):
            time.sleep(2)
            try:
                response = http.get('/api/sennheiser/state')
                if response.status_code != 200:
                    continue
                state = response.json()
                source_ids = {m['id'] for m in original_state['settings'].get('microphones', []) if m.get('enabled')}
                statuses = {m['id']: m for m in state.get('microphones', [])}
                primary = state.get('mic', {})
                ok = primary.get('state') == 'Connected' and primary.get('age') is not None and primary['age'] < 3
                ok = ok and all(statuses.get(i, {}).get('state') == 'Connected' and statuses[i].get('age') is not None and statuses[i]['age'] < 3 for i in source_ids)
                ok = ok and (not original_state.get('speech', {}).get('enabled') or state.get('speech', {}).get('healthy'))
                if ok:
                    recovered = state
                    break
            except (httpx.HTTPError, KeyError, ValueError):
                continue
        if not recovered:
            raise RuntimeError('Microphones or speech service did not recover in time')
        if settings_hash(recovered) != original_hash:
            raise RuntimeError('Seat or preset settings changed during restart')
        for path in ('/viewer', '/html/setup.html'):
            response = http.get(path); response.raise_for_status()
        if paused or '--restore-auto' in sys.argv:
            response = http.post('/api/set_layout_mode', json={'mode':'auto', 'sub_mode':original_mode.get('sub_mode')}); response.raise_for_status()
            if not response.json().get('success'):
                raise RuntimeError('Could not restore automatic tracking')
            paused = False
        print('PASS: services, both microphones, speech gate, viewer and unchanged seat settings.')
        print('Backup:', backup)
    except BaseException:
        if modified:
            print('Deployment failed; restoring source backup.', flush=True)
            for rel in modified:
                source = backup + '/' + rel
                target = '/home/ordain/' + rel
                run(client, 'cp -p ' + shlex.quote(source) + ' ' + shlex.quote(target))
            if PHASE4:
                run(client, 'sudo -n /usr/bin/systemctl restart stream_manager.service')
            else:
                run(client, 'sudo -n /usr/bin/systemctl restart confdeck.service')
            if not (PHASE2 or PHASE3 or PHASE4 or PHASE6 or PHASE7 or PHASE8):
                run(client, 'sudo -n /usr/bin/systemctl restart tracking_backend.service')
        if paused:
            print('Automatic tracking remains paused; verify the room before resuming.', flush=True)
        raise
    finally:
        sftp.close()
        client.close()
        http.close()


if __name__ == '__main__':
    main()
