"""Unified safe deployment for Mini PC (.31): alters ONLY Port 4000 (OmniTrack / tracking_backend.service).

ConfDeck (port 8765 / confdeck.service) is never modified or restarted.
"""

import argparse
import hashlib
import os
import posixpath
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Strictly deploy Port 4000 (OmniTrack) files only — never touch /home/ordain/sehnizer (port 8765).
CONFDECK_FILES: list[tuple[str, str]] = []

OMNITRACK_FILES = [
    (
        "artifacts/mini31-candidate/omnitrack-sennheiser/backend_services/routes/sennheiser_routes.py",
        "/home/ordain/omnitrack-sennheiser/backend_services/routes/sennheiser_routes.py",
    ),
    (
        "artifacts/mini31-candidate/omnitrack-sennheiser/public/html/setup.html",
        "/home/ordain/omnitrack-sennheiser/public/html/setup.html",
    ),
    (
        "artifacts/mini31-candidate/omnitrack-sennheiser/public/js/modules/vision_module.js",
        "/home/ordain/omnitrack-sennheiser/public/js/modules/vision_module.js",
    ),
    (
        "artifacts/mini31-candidate/omnitrack-sennheiser/public/js/modules/sennheiser_module.js",
        "/home/ordain/omnitrack-sennheiser/public/js/modules/sennheiser_module.js",
    ),
    (
        "artifacts/mini31-candidate/omnitrack-sennheiser/public/css/premium.css",
        "/home/ordain/omnitrack-sennheiser/public/css/premium.css",
    ),
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_manifest() -> list[tuple[Path, str, str]]:
    manifest = []
    for rel, remote in CONFDECK_FILES + OMNITRACK_FILES:
        local = ROOT / rel
        if not local.is_file():
            raise FileNotFoundError(f"Missing local deploy file: {local}")
        manifest.append((local, remote, sha256_bytes(local.read_bytes())))
    return manifest


def run_ssh(ssh, cmd: str, sudo_pass: str | None = None, check: bool = True) -> tuple[int, str, str]:
    if sudo_pass:
        stdin, stdout, stderr = ssh.exec_command(f"sudo -S -p '' {cmd}", timeout=60)
        stdin.write(sudo_pass + "\n")
        stdin.flush()
    else:
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if check and code != 0:
        raise RuntimeError(f"Command failed ({code}): {cmd}\nSTDOUT: {out}\nSTDERR: {err}")
    return code, out, err


def ensure_remote_dir(sftp, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    cur = ""
    for part in parts:
        cur = f"{cur}/{part}"
        try:
            sftp.stat(cur)
        except OSError:
            sftp.mkdir(cur)


def deploy(host: str, user: str, password: str, dry_run: bool) -> int:
    manifest = collect_manifest()
    print(f"Manifest verified locally ({len(manifest)} files):")
    for local, remote, digest in manifest:
        print(f"  {local.relative_to(ROOT)} -> {remote} [{digest[:12]}]")

    if dry_run:
        print("Dry-run complete (no SSH/network calls made).")
        return 0

    import paramiko

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = f"/home/{user}/backups/unified_{stamp}"
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    try:
        sftp = ssh.open_sftp()
        try:
            for local, remote, expected_sha in manifest:
                # Backup existing remote file if present
                run_ssh(
                    ssh,
                    f"bash -c 'if [ -f \"{remote}\" ]; then mkdir -p \"{backup_root}\" && cp \"{remote}\" \"{backup_root}/{posixpath.basename(remote)}\"; fi'",
                )
                ensure_remote_dir(sftp, posixpath.dirname(remote))
                tmp_remote = f"{remote}.tmp.{stamp}"
                sftp.put(str(local), tmp_remote)
                run_ssh(ssh, f"mv \"{tmp_remote}\" \"{remote}\"")
                _, out, _ = run_ssh(ssh, f"sha256sum \"{remote}\"")
                actual_sha = out.strip().split()[0]
                if actual_sha != expected_sha:
                    raise RuntimeError(f"SHA mismatch on {remote}: expected {expected_sha}, got {actual_sha}")
                print(f"  [OK] {remote} ({actual_sha[:12]})")
        finally:
            sftp.close()

        py_targets = [r for _, r, _ in manifest if r.endswith(".py")]
        run_ssh(ssh, "python3 -m py_compile " + " ".join(f'"{p}"' for p in py_targets))
        print("[OK] Remote py_compile passed.")

        run_ssh(ssh, "systemctl restart tracking_backend.service", sudo_pass=password)
        time.sleep(3.0)
        run_ssh(ssh, "systemctl is-active tracking_backend.service confdeck.service")
        print("[OK] tracking_backend.service restarted; confdeck.service untouched and active.")

        smoke_cmd = (
            "python3 -c \"import urllib.request, json; "
            "cams = json.loads(urllib.request.urlopen('http://127.0.0.1:4000/api/sennheiser/vision/cameras').read().decode()); "
            "print('port4000 cams:', [(c['key'], c['id'], c['mode']) for c in cams.get('cameras', [])]); "
            "st = json.loads(urllib.request.urlopen('http://127.0.0.1:4000/api/sennheiser/accuracy/vision/state').read().decode()); "
            "print('port4000 state mode:', st.get('mode'))\""
        )
        _, out, _ = run_ssh(ssh, smoke_cmd)
        print(out.strip())
    finally:
        ssh.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy unified AI Vision + Port 4000 integration to Mini PC.")
    parser.add_argument("--host", default=os.environ.get("MINIPC_HOST", "192.168.1.31"))
    parser.add_argument("--user", default=os.environ.get("MINIPC_USER", "ordain"))
    parser.add_argument("--password", default=os.environ.get("MINIPC_PASS", "0208"))
    parser.add_argument("--dry-run", action="store_true", help="Verify local manifest and checksums without SSH.")
    args = parser.parse_args()
    return deploy(args.host, args.user, args.password, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
