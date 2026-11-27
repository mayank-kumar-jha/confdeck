from types import SimpleNamespace

from scripts.kiosk_watch import Kiosk, browser_command, connected_outputs


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = 0

    def wait(self, timeout):
        return self.code


def test_browser_local_viewer_and_sandbox():
    args = browser_command('/usr/bin/chromium', '/tmp/profile')
    assert args[-1] == 'http://127.0.0.1:4000/viewer'
    assert '--kiosk' in args and '--no-sandbox' not in args
    assert '--no-sandbox' in browser_command('chromium', '/tmp/profile', True)


def test_hdmi_hotplug_crash_restart_and_no_repeated_mode_writes(monkeypatch):
    monkeypatch.setattr('scripts.kiosk_watch.os.geteuid', lambda: 1000, raising=False)
    clock = [0.]
    screen = ['HDMI-1 disconnected']
    calls, processes = [], []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=screen[0])
    def spawn(args):
        process = Process()
        processes.append(process)
        return process
    kiosk = Kiosk('/tmp/profile', run=run, spawn=spawn, ready=lambda: True,
                  browser=lambda: '/usr/bin/chromium', now=lambda: clock[0])
    kiosk.step()
    assert not processes
    screen[0] = 'HDMI-1 connected primary 1920x1080+0+0'
    kiosk.step()
    assert len(processes) == 1
    for _ in range(5):
        kiosk.step()
    assert len([c for c in calls if '--output' in c]) == 1
    assert len(processes) == 1
    processes[-1].code = 1
    clock[0] = 11
    kiosk.step()
    assert len(processes) == 2
    screen[0] = 'HDMI-1 disconnected'
    kiosk.step()
    assert processes[-1].code == 0
    screen[0] = 'HDMI-1 connected'
    clock[0] = 22
    kiosk.step()
    assert len(processes) == 3
    kiosk.stop()


def test_backend_outage_and_xrandr_failure_do_not_spawn(monkeypatch):
    monkeypatch.setattr('scripts.kiosk_watch.os.geteuid', lambda: 1000, raising=False)
    status = [1]
    spawned = []
    kiosk = Kiosk('/tmp/profile', run=lambda *a, **kw: SimpleNamespace(returncode=status[0], stdout='HDMI-1 connected'),
                  spawn=lambda args: spawned.append(args), ready=lambda: False, browser=lambda: 'chromium')
    kiosk.step()
    status[0] = 0
    kiosk.step()
    assert not spawned
    assert connected_outputs('DP-1 connected\nHDMI-2 disconnected\nHDMI-1 connected') == ('DP-1', 'HDMI-1')
