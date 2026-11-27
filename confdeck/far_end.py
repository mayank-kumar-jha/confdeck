"""Optional far-end USB activity monitor; audio stays in memory."""
import array
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time


def inputs(root=Path('/proc/asound')):
    result = []
    try:
        lines = (root / 'pcm').read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        match = re.match(r'^(\d+)-(\d+):\s*(.*?)\s*:\s*.*capture\s+\d+', line)
        if not match:
            continue
        card, device, name = match.groups()
        try:
            identity = (root / ('card' + str(int(card))) / 'id').read_text().strip()
        except OSError:
            continue
        if re.fullmatch(r'[A-Za-z0-9_]{1,32}', identity):
            result.append({'id': identity + ':' + str(int(device)), 'name': name + ' (' + identity + ')'})
    return result


class FarEndMonitor:
    def __init__(self, root):
        self.path = root / 'far-end-monitor.json'
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None
        self.last_frame = None
        self.active_until = 0
        self.level = None
        self.error = ''
        self.settings = {'enabled': False, 'device': '', 'channel': 1, 'channels': 2,
                         'threshold_db': -40, 'hold_ms': 800}
        if self.path.exists():
            try:
                self.settings = self.validate(json.loads(self.path.read_text()))
            except (ValueError, OSError, TypeError):
                self.settings['enabled'] = True
                self.error = 'Saved far-end settings are invalid'
        if self.settings['enabled'] and not self.error:
            self.start()

    @staticmethod
    def validate(settings):
        if not isinstance(settings, dict) or set(settings) != {'enabled', 'device', 'channel', 'channels', 'threshold_db', 'hold_ms'}:
            raise ValueError('Invalid far-end settings')
        enabled, device = settings['enabled'], settings['device']
        channel, channels, threshold, hold = (settings[x] for x in ('channel', 'channels', 'threshold_db', 'hold_ms'))
        if type(enabled) is not bool or not isinstance(device, str) or (device and not re.fullmatch(r'[A-Za-z0-9_]{1,32}:\d{1,2}', device)):
            raise ValueError('Choose a valid input device')
        if enabled and not device:
            raise ValueError('Select a dedicated far-end audio input')
        if type(channel) is not int or type(channels) is not int or channels not in (1, 2) or not 1 <= channel <= channels:
            raise ValueError('Choose a valid input channel')
        if type(threshold) not in (int, float) or not -80 <= threshold <= -10 or type(hold) is not int or not 100 <= hold <= 5000:
            raise ValueError('Invalid threshold or hold time')
        return dict(settings)

    def configure(self, settings):
        settings = self.validate(settings)
        if settings['enabled'] and settings['device'] not in {d['id'] for d in inputs()}:
            raise ValueError('Selected audio input is not connected')
        self.stop()
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(settings), encoding='utf8')
        os.replace(temporary, self.path)
        with self.lock:
            self.settings, self.error = settings, ''
            self.last_frame, self.level, self.active_until = None, None, 0
        if settings['enabled']:
            self.start()

    def test_input(self, settings):
        settings = self.validate(settings)
        if self.settings['enabled']:
            raise ValueError('Disable monitoring before testing the input')
        if settings['device'] not in {d['id'] for d in inputs()}:
            raise ValueError('Selected audio input is not connected')
        card, device = settings['device'].split(':')
        cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'alsa',
               '-channels', str(settings['channels']), '-sample_rate', '48000',
               '-i', f'plughw:CARD={card},DEV={device}', '-t', '1', '-f', 's16le',
               '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', str(settings['channels']), 'pipe:1']
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=6)
        if result.returncode or not result.stdout:
            raise ValueError('No input frames received')
        samples = array.array('h', result.stdout)[settings['channel']-1::settings['channels']]
        if not samples:
            raise ValueError('No samples on that channel')
        power = sum(sample * sample for sample in samples) / len(samples)
        return {'level_db': round(20 * math.log10(max(math.sqrt(power) / 32768, 1e-6)), 1)}

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.process:
            self.process.terminate()
        if self.thread:
            self.thread.join(timeout=3)
        self.thread, self.process = None, None

    def snapshot(self):
        with self.lock:
            enabled = self.settings['enabled']
            healthy = enabled and self.last_frame is not None and time.monotonic() - self.last_frame < 1.5
            active = healthy and time.monotonic() < self.active_until
            return {'settings': dict(self.settings), 'healthy': healthy, 'active': active,
                    'blocked': enabled and (not healthy or active),
                    'level_db': self.level, 'error': self.error}

    def _worker(self):
        settings = self.settings.copy()
        card, device = settings['device'].split(':')
        cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'alsa',
               '-channels', str(settings['channels']), '-sample_rate', '48000',
               '-i', f'plughw:CARD={card},DEV={device}', '-f', 's16le', '-acodec', 'pcm_s16le',
               '-ar', '16000', '-ac', str(settings['channels']), 'pipe:1']
        while not self.stop_event.is_set():
            try:
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.process = process
                size = 320 * settings['channels'] * 2
                while not self.stop_event.is_set():
                    data = process.stdout.read(size)
                    if len(data) != size:
                        break
                    samples = array.array('h', data)[settings['channel']-1::settings['channels']]
                    power = sum(sample * sample for sample in samples) / len(samples)
                    level = 20 * math.log10(max(math.sqrt(power) / 32768, 1e-6))
                    now = time.monotonic()
                    with self.lock:
                        self.last_frame, self.level, self.error = now, round(level, 1), ''
                        if level >= settings['threshold_db']:
                            self.active_until = now + settings['hold_ms'] / 1000
                if not self.stop_event.is_set():
                    with self.lock:
                        self.last_frame, self.error = None, 'Far-end input stopped; camera triggers paused'
            except OSError:
                with self.lock:
                    self.last_frame, self.error = None, 'Far-end input unavailable; camera triggers paused'
            finally:
                if self.process:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                    self.process = None
            self.stop_event.wait(2)
