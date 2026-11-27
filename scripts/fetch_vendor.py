"""Build-time downloads only. ConfDeck never calls this script at runtime."""

import hashlib
import io
import json
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "vendor" / "bin"
STATIC = ROOT / "confdeck" / "static" / "vendor"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ConfDeck-bundle-builder"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main():
    BIN.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)
    records = []

    def save(url, destination):
        data = fetch(url)
        destination.write_bytes(data)
        records.append(
            {
                "url": url,
                "file": str(destination.relative_to(ROOT)),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        print(destination.name, len(data), flush=True)

    win = platform.system() == "Windows"
    suffix = "windows_amd64.zip" if win else "linux_amd64.tar.gz"
    version = "v1.21.3"
    url = f"https://github.com/bluenviron/mediamtx/releases/download/{version}/mediamtx_{version}_{suffix}"
    data = fetch(url)
    records.append({"url": url, "sha256": hashlib.sha256(data).hexdigest()})
    if win:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            (BIN / "mediamtx.exe").write_bytes(z.read("mediamtx.exe"))
            (BIN / "MEDIAMTX-LICENSE").write_bytes(z.read("LICENSE"))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            (BIN / "mediamtx").write_bytes(tar.extractfile("mediamtx").read())
            (BIN / "mediamtx").chmod(0o755)
    print("MediaMTX downloaded", flush=True)
    save("https://cdn.jsdelivr.net/npm/hls.js@1.6.13/dist/hls.min.js", STATIC / "hls.min.js")
    save("https://cdn.jsdelivr.net/npm/hls.js@1.6.13/LICENSE", STATIC / "HLS-LICENSE")
    save(
        "https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@5.2.8/files/inter-latin-wght-normal.woff2",
        STATIC / "inter.woff2",
    )
    save("https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt", STATIC / "FONT-LICENSE")
    if win and not (BIN / "ffprobe.exe").exists():
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        data = fetch(url)
        records.append({"url": url, "sha256": hashlib.sha256(data).hexdigest()})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
                found = next(
                    p for p in z.namelist() if p.endswith("/bin/" + name) or p.endswith("/" + name)
                )
                (BIN / (name if name.endswith(".exe") else "FFMPEG-LICENSE")).write_bytes(
                    z.read(found)
                )
        print("FFmpeg and ffprobe downloaded", flush=True)
    (ROOT / "vendor" / "manifest.json").write_text(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
