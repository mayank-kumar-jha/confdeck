"""Linux HDMI viewer supervisor. No camera, network or room-setting writes."""
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

VIEWER = "http://127.0.0.1:4000/viewer"


def connected_outputs(text):
    return tuple(re.findall(r"^([\w.-]+) connected\b", text, re.MULTILINE))


def browser_command(executable, profile, root=False):
    args = [executable, "--kiosk", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", "--disable-session-crashed-bubble",
            "--disable-background-networking", "--password-store=basic",
            f"--user-data-dir={profile}", VIEWER]
    # Some existing mini-PC services run as root; preserve that compatibility.
    # Non-root sessions retain Chromium's sandbox.
    if root:
        args.insert(1, "--no-sandbox")
    return args


def find_browser():
    # The deployed kiosk already uses this executable successfully under xinit.
    legacy = Path("/snap/chromium/current/usr/lib/chromium-browser/chrome")
    if legacy.is_file():
        return str(legacy)
    for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def viewer_ready():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(VIEWER, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


class Kiosk:
    def __init__(self, profile, run=subprocess.run, spawn=subprocess.Popen,
                 ready=viewer_ready, browser=find_browser, now=time.monotonic):
        self.profile, self.run, self.spawn = profile, run, spawn
        self.ready, self.browser, self.now = ready, browser, now
        self.outputs = None
        self.process = None
        self.retry_at = 0.
        self.message = None

    def status(self, text):
        if text != self.message:
            print(text, flush=True)
            self.message = text

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None

    def step(self):
        try:
            result = self.run(["xrandr", "--query"], capture_output=True, text=True, timeout=3)
            if result.returncode:
                self.status("Waiting for the graphical display session")
                return
            outputs = connected_outputs(result.stdout)
            if not outputs:
                self.stop()
                self.outputs = ()
                self.status("Waiting for HDMI/DisplayPort connection")
                return
            if outputs != self.outputs:
                primary = outputs[0]
                for output in outputs:
                    args = ["xrandr", "--output", output, "--auto"]
                    if output == primary:
                        args += ["--primary", "--pos", "0x0"]
                    else:
                        args += ["--same-as", primary]
                    configured = self.run(args, capture_output=True, text=True, timeout=3)
                    if configured.returncode:
                        self.status("Display mode failed; retrying")
                        return
                # Recreate the fullscreen window only when monitor topology changes.
                self.stop()
                self.outputs = outputs
            if self.process and self.process.poll() is None:
                return
            if self.now() < self.retry_at:
                return
            self.retry_at = self.now()+10
            executable = self.browser()
            if not executable:
                self.status("No Chromium-compatible browser installed")
                return
            if not self.ready():
                self.status("Waiting for Smart Preview on localhost:4000/viewer")
                return
            self.process = self.spawn(browser_command(executable, self.profile, os.geteuid() == 0))
            self.status("Smart Preview fullscreen viewer started")
        except (OSError, subprocess.SubprocessError) as error:
            self.status(f"Display retry: {type(error).__name__}")


def main():
    import fcntl
    directory = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home()/".cache")))/"omnitrack-kiosk"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/"watch.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("HDMI viewer supervisor already running", flush=True)
            return
        kiosk = Kiosk(directory/"browser")
        def finish(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, finish)
        signal.signal(signal.SIGINT, finish)
        try:
            while True:
                kiosk.step()
                time.sleep(5)
        finally:
            kiosk.stop()


if __name__ == "__main__":
    main()
