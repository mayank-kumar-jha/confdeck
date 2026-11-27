import hashlib
import time
from pathlib import Path
import paramiko

MINI_PC_IP = "192.168.1.31"
USER = "ordain"
PASSWORD = "0208"

ROOT = Path(__file__).resolve().parents[1]

FILES_TO_DEPLOY = [
    ("confdeck/visca.py", "/home/ordain/sehnizer/confdeck/visca.py"),
    ("confdeck/vision/body.py", "/home/ordain/sehnizer/confdeck/vision/body.py"),
    ("confdeck/vision/mouth.py", "/home/ordain/sehnizer/confdeck/vision/mouth.py"),
    ("confdeck/evaluation/lab.py", "/home/ordain/sehnizer/confdeck/evaluation/lab.py"),
    ("confdeck/evaluation/api.py", "/home/ordain/sehnizer/confdeck/evaluation/api.py"),
    ("confdeck/static/accuracy-lab.js", "/home/ordain/sehnizer/confdeck/static/accuracy-lab.js"),
]

def deploy():
    print(f"Connecting to Mini PC at {MINI_PC_IP}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(MINI_PC_IP, username=USER, password=PASSWORD, timeout=8)
    sftp = ssh.open_sftp()

    def run_cmd(cmd):
        _, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        return out, err

    backup_dir = "/home/ordain/integration-backups"
    run_cmd(f"mkdir -p {backup_dir}")
    ts = time.strftime("%Y%m%d-%H%M%S")

    for local_rel, remote_path in FILES_TO_DEPLOY:
        local_file = ROOT / local_rel
        print(f"\nProcessing {local_rel} -> {remote_path}...")
        
        # Backup remote
        try:
            with sftp.open(remote_path, 'rb') as f:
                remote_bytes = f.read()
            bname = Path(remote_path).name
            backup_file = f"{backup_dir}/{bname}.backup-{ts}"
            with sftp.open(backup_file, 'wb') as f:
                f.write(remote_bytes)
            print(f"  Backed up existing remote file to: {backup_file}")
        except Exception as e:
            print(f"  Note on remote file: {e}")

        # Transfer local file with normalized LF line endings
        local_bytes = local_file.read_bytes().replace(b"\r\n", b"\n")
        local_hash = hashlib.sha256(local_bytes).hexdigest()
        
        with sftp.open(remote_path, 'wb') as f:
            f.write(local_bytes)

        # Verify
        with sftp.open(remote_path, 'rb') as f:
            verified_bytes = f.read()
        verified_hash = hashlib.sha256(verified_bytes).hexdigest()
        assert verified_hash == local_hash, f"Hash mismatch on {remote_path}!"
        print(f"  Verified SHA256 matches: {verified_hash[:12]} ({len(verified_bytes)} bytes)")

    # Enable VISCA control for Camera 128 in settings
    print("\nConfiguring Camera 128 settings for VISCA control on Mini PC...")
    py_config = '''
from confdeck.store import Store
from pathlib import Path
store = Store(Path('/home/ordain/.local/share/ConfDeck'))
settings = store.load()
for cam in settings.cameras:
    if cam.ip == '192.168.1.128' or cam.id == '0637f621814b':
        cam.control_protocol = 'visca'
        cam.control_enabled = True
        cam.visca_port = 52381
        cam.control_port = 80
        print(f"Updated Camera {cam.id} to VISCA control_enabled=True")
store.save(settings)
'''
    import base64
    b64_cfg = base64.b64encode(py_config.encode()).decode()
    out, err = run_cmd(f"/home/ordain/sehnizer/.venv/bin/python -c \"import base64; exec(base64.b64decode('{b64_cfg}').decode())\"")
    print(out)

    # Restart confdeck service
    print("\nRestarting confdeck systemd service...")
    run_cmd(f"echo {PASSWORD} | sudo -S systemctl restart confdeck")
    time.sleep(2.0)
    out, _ = run_cmd("systemctl is-active confdeck")
    print(f"  confdeck service status after restart: {out.strip()}")

    # Verify remote Python imports and detector status
    print("\nVerifying remote Python runtime...")
    test_cmd = "/home/ordain/sehnizer/.venv/bin/python -c 'from confdeck.vision.mouth import SeatVisionDetector; d = SeatVisionDetector(); print(\"SeatVisionDetector loaded! score_threshold:\", d.score_threshold, \"speech_threshold:\", d.speech_threshold)'"
    out, err = run_cmd(test_cmd)
    print(f"  {out}")
    if err:
        print(f"  Warnings: {err}")

    sftp.close()
    ssh.close()
    print("\nAll files deployed and verified successfully!")

if __name__ == "__main__":
    deploy()
