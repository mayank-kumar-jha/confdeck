"""Refresh this workspace's generated Windows bundle after verified source fixes."""

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = (ROOT / "bundle" / "ConfDeck-Windows").resolve()


def main():
    if (
        not TARGET.is_relative_to((ROOT / "bundle").resolve())
        or not (TARGET / "BUILD.json").is_file()
    ):
        raise SystemExit("The generated Windows bundle was not found. Run build_bundle.py first.")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(TARGET / "wheels"),
            str(ROOT),
        ],
        check=True,
    )
    shutil.copytree(
        ROOT / "confdeck",
        TARGET / "runtime" / "Lib" / "site-packages" / "confdeck",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in (
        "README.md",
        "THIRD_PARTY.md",
        "Start ConfDeck.cmd",
        "Start Demo.cmd",
        "Check Setup.cmd",
        "confdeck.cmd",
        "install-offline.sh",
        "confdeck.sh",
    ):
        shutil.copy2(ROOT / name, TARGET / name)
    (TARGET / "docs").mkdir(exist_ok=True)
    for name in ("ROOM_INSTALLATION.md", "MULTIMIC_AUDIT.md"):
        shutil.copy2(ROOT / "docs" / name, TARGET / "docs" / name)
    (TARGET / "docs" / "reports").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "docs/reports/multimic-benchmark.json",
                 TARGET / "docs/reports/multimic-benchmark.json")
    hashes = {}
    for path in sorted(TARGET.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.name != "SHA256SUMS.json":
            with path.open("rb") as source:
                hashes[path.relative_to(TARGET).as_posix()] = hashlib.file_digest(
                    source, "sha256"
                ).hexdigest()
    (TARGET / "SHA256SUMS.json").write_text(json.dumps(hashes, indent=2))
    archive = TARGET.with_suffix(".zip")
    staging = TARGET.with_suffix(".zip.tmp")
    with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        for path in sorted(TARGET.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                z.write(path, Path(TARGET.name) / path.relative_to(TARGET))
    staging.replace(archive)
    print(f"Updated offline bundle: {archive}")


if __name__ == "__main__":
    main()
