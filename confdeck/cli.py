"""Offline launcher, instance lock and operator-friendly preflight checks."""

import argparse
import asyncio
import contextlib
import os
import signal
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn

from confdeck import __version__
from confdeck.app import create_app
from confdeck.discovery import networks
from confdeck.media import binary
from confdeck.offline import install_guard
from confdeck.runtime import Runtime
from confdeck.security import setup_logs


def data_directory(demo=False):
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / ("ConfDeck-demo" if demo else "ConfDeck")


class InstanceLock:
    """OS-owned file lock, automatically released after a crash."""

    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.file = open(root / "instance.lock", "a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError(
                "ConfDeck is already running with this data folder. Open its browser window."
            ) from None

    def close(self):
        self.file.close()


def free_port(host, port, udp=False):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as s:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def doctor(root, display_host="127.0.0.1") -> int:
    """Check binaries, interfaces, writable data and required ports without Internet."""
    print(f"ConfDeck {__version__} · offline checks")
    failures = 0
    for name in ("mediamtx", "ffprobe", "ffmpeg"):
        available = binary(name)
        print(
            f"{'PASS' if available else 'WARN'} {name}: {available or 'Missing. Copy the matching executable into vendor/bin from the offline bundle.'}"
        )
        if not available and name != "ffmpeg":
            failures += 1
    for port, label in (
        (8765, "admin"),
        (8766, "display"),
        (8554, "camera relay"),
        (8888, "HLS video"),
        (8889, "WebRTC signaling"),
        (9997, "relay API"),
        (8189, "WebRTC transport"),
    ):
        ok = free_port(display_host if port in (8766, 8189) else "127.0.0.1", port)
        print(
            f"{'PASS' if ok else 'WARN'} Port {port} ({label}): {'available' if ok else 'in use; expected if ConfDeck is running. Otherwise close the conflicting app.'}"
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / ".write-check"
        path.write_text("ok")
        path.unlink()
        print(f"PASS Writable settings folder: {root}")
    except OSError:
        failures += 1
        print("FAIL Settings folder is not writable. Use --data-dir with a folder you own.")
    udp_ok = free_port(display_host, 8189, udp=True)
    print(f"{'PASS' if udp_ok else 'WARN'} UDP 8189 (WebRTC): "
          f"{'available' if udp_ok else 'in use; expected while ConfDeck is running.'}")
    interfaces = networks()
    print(
        f"{'PASS' if interfaces else 'WARN'} Local networks: {', '.join(n['subnet'] for n in interfaces) or 'none; connect the camera network cable.'}"
    )
    print("PASS No CDN, telemetry, online STUN, model downloads or update checks in the runtime.")
    print(
        "WARN Glass-to-glass latency and real-device compatibility require an on-site test. See README."
    )
    return 1 if failures else 0


class Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(args, root):
    runtime = Runtime(root, args.demo, args.display_host, args.port, args.display_port)
    setup_logs(root)
    servers = [
        Server(
            uvicorn.Config(
                create_app(runtime),
                host="127.0.0.1",
                port=args.port,
                log_level="warning",
                access_log=False,
                workers=1,
                ws_max_size=65536,
            )
        ),
        Server(
            uvicorn.Config(
                create_app(runtime, display=True),
                host=args.display_host,
                port=args.display_port,
                log_level="warning",
                access_log=False,
                workers=1,
                ws_max_size=65536,
            )
        ),
    ]
    stopping = asyncio.Event()

    def stop(*unused):
        stopping.set()
        for server in servers:
            server.should_exit = True

    runtime.request_shutdown = stop

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    print(
        f"\nConfDeck {'DEMO' if args.demo else ''} {__version__}\nAdmin: http://127.0.0.1:{args.port}\nLocal admin: PIN disabled\nDisplay: http://{args.display_host}:{args.display_port}/display\nData: {root}\nPress Ctrl+C to stop.\n",
        flush=True,
    )
    tasks = []
    listener_lock = asyncio.Lock()

    async def change_display_host(host):
        async with listener_lock:
            if host == runtime.display_host:
                return
            if not free_port(host, args.display_port):
                raise ValueError("The display port is in use on that network address.")
            servers[-1].should_exit = True
            await asyncio.wait_for(asyncio.shield(tasks[-1]), 8)
            runtime.display_host = host
            runtime.media.display_host = host
            runtime.settings.display_host = host
            runtime.save()
            replacement = Server(
                uvicorn.Config(
                    create_app(runtime, display=True),
                    host=host,
                    port=args.display_port,
                    log_level="warning",
                    access_log=False,
                    ws_max_size=65536,
                )
            )
            servers.append(replacement)
            tasks.append(asyncio.create_task(replacement.serve()))
            if runtime.media.process and runtime.media.process.returncode is None:
                runtime.media.process.terminate()

    runtime.change_display_host = change_display_host
    try:
        await runtime.start()
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        if not args.no_browser:
            await asyncio.sleep(1)
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        await asyncio.gather(*tasks)
    finally:
        stop()
        await runtime.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    """Start the local control desk or run non-destructive diagnostics."""
    parser = argparse.ArgumentParser(
        prog="confdeck", description="Offline meeting-room control desk"
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Start ConfDeck and open the browser")
    start.add_argument(
        "--demo", action="store_true", help="Use local microphone simulator; cameras always require real connections"
    )
    start.add_argument("--no-browser", action="store_true")
    start.add_argument("--port", type=int, default=8765)
    start.add_argument("--display-port", type=int, default=8766)
    for p in (start, sub.add_parser("doctor", help="Check offline prerequisites")):
        p.add_argument("--data-dir", type=Path)
        p.add_argument(
            "--display-host",
            default=None,
            help="Optional private LAN IPv4 address for the display listener",
        )
    args = parser.parse_args()
    from confdeck.models import local_ip

    try:
        root = (args.data_dir or data_directory(getattr(args, "demo", False))).resolve()
        if args.display_host is None:
            args.display_host = "127.0.0.1"
            backup = root / "settings.backup.json"
            if backup.exists():
                from confdeck.models import Settings

                with contextlib.suppress(ValueError, OSError):
                    saved = Settings.model_validate_json(backup.read_text()).display_host
                    if saved in {n["ip"] for n in networks()}:
                        args.display_host = saved
        args.display_host = local_ip(args.display_host)
        install_guard()
        if args.command == "doctor":
            raise SystemExit(doctor(root, args.display_host))
        if args.port == args.display_port or not all(
            1024 <= p <= 65535 for p in (args.port, args.display_port)
        ):
            raise ValueError("Use two different ports between 1024 and 65535.")
        lock = InstanceLock(root)
        try:
            if not free_port("127.0.0.1", args.port) or not free_port(
                args.display_host, args.display_port
            ):
                raise RuntimeError(
                    "The admin or display port is already in use. Close the other ConfDeck window or choose different ports."
                )
            asyncio.run(serve(args, root))
        finally:
            lock.close()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            f"ConfDeck could not start: {exc}\nRun confdeck doctor for checks and fixes.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
