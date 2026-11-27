"""Build an offline installer on a connected preparation machine.

Windows: portable embedded Python plus installed dependencies and a wheelhouse.
Linux: wheelhouse + installer for the same Python ABI/platform; Python 3.11+
and Python must be supplied by the target machine or offline OS media.
The script is never imported by the runtime.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "bundle" / "ConfDeck-Windows")
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(
            "Choose a new empty bundle directory. Existing bundles are never overwritten."
        )
    target.mkdir(parents=True, exist_ok=True)
    wheels = target / "wheels"
    wheels.mkdir()
    requirement = str(ROOT)
    run(
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--disable-pip-version-check",
        "--timeout",
        "120",
        "--wheel-dir",
        wheels,
        requirement,
    )
    app_wheel = next(wheels.glob("confdeck-*.whl"))
    for name in (
        "README.md",
        "LICENSE",
        "THIRD_PARTY.md",
        "Start ConfDeck.cmd",
        "Start Demo.cmd",
        "Check Setup.cmd",
        "confdeck.cmd",
        "install-offline.sh",
        "confdeck.sh",
    ):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    shutil.copytree(ROOT / "vendor" / "bin", target / "vendor" / "bin")
    (target / "docs").mkdir(exist_ok=True)
    for name in ("ROOM_INSTALLATION.md", "MULTIMIC_AUDIT.md"):
        shutil.copy2(ROOT / "docs" / name, target / "docs" / name)
    (target / "docs" / "reports").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "docs/reports/multimic-benchmark.json",
                 target / "docs/reports/multimic-benchmark.json")
    shutil.copy2(ROOT / "vendor" / "manifest.json", target / "vendor" / "manifest.json")
    if os.name == "nt":
        version = ".".join(map(str, sys.version_info[:3]))
        if sys.version_info[:2] != (3, 12) or platform.machine().lower() not in ("amd64", "x86_64"):
            raise SystemExit("Build the portable Windows bundle with 64-bit Python 3.12.")
        url = f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
        archive = target / "python-embedded.zip"
        urllib.request.urlretrieve(url, archive)
        runtime = target / "runtime"
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                destination = (runtime / info.filename).resolve()
                if not destination.is_relative_to(runtime.resolve()):
                    raise RuntimeError("Python archive contains an unsafe path")
            z.extractall(runtime)
        packages = runtime / "Lib" / "site-packages"
        packages.mkdir(parents=True)
        # Host pip performs a fully offline installation into embedded Python.
        run(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            wheels,
            "--target",
            packages,
            "confdeck",
        )
        (runtime / "python312._pth").write_text(
            "python312.zip\n.\nLib/site-packages\nimport site\n"
        )
        env = dict(os.environ, CONFDECK_BIN=str(target / "vendor" / "bin"))
        subprocess.run(
            [
                str(runtime / "python.exe"),
                "-m",
                "confdeck",
                "doctor",
                "--data-dir",
                str(target / "doctor-data"),
            ],
            env=env,
            check=True,
        )
        # Doctor's test directory is empty after its write test.
        (target / "doctor-data").rmdir()
    else:
        for name in ("confdeck.sh", "install-offline.sh"):
            (target / name).chmod(0o755)
    hashes = {}
    for path in sorted(target.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            hashes[path.relative_to(target).as_posix()] = hashlib.file_digest(
                path.open("rb"), "sha256"
            ).hexdigest()
    (target / "SHA256SUMS.json").write_text(json.dumps(hashes, indent=2))
    (target / "BUILD.json").write_text(
        json.dumps(
            {
                "app_wheel": app_wheel.name,
                "python": sys.version,
                "platform": platform.platform(),
                "install_requires_internet": False,
            },
            indent=2,
        )
    )
    if args.zip:
        archive = shutil.make_archive(str(target), "zip", target.parent, target.name)
        print(f"Offline bundle: {archive}")
    else:
        print(f"Offline bundle folder: {target}")


if __name__ == "__main__":
    main()
