import json
import array
import queue
import threading
import time

from confdeck.far_end import FarEndMonitor, inputs


def test_capture_devices_include_only_capture(tmp_path):
    (tmp_path / 'pcm').write_text('01-00: USB Audio : USB Audio : playback 1 : capture 1\n00-03: HDMI : HDMI : playback 1\n')
    (tmp_path / 'card1').mkdir()
    (tmp_path / 'card1/id').write_text('Soundcard\n')
    assert inputs(tmp_path) == [{'id': 'Soundcard:0', 'name': 'USB Audio (Soundcard)'}]


async def test_missing_signal_blocks_only_when_enabled(runtime, client, monkeypatch, tmp_path):
    monkeypatch.setattr('confdeck.far_end.inputs', lambda: [{'id': 'Soundcard:0'}])
    monkeypatch.setattr(runtime.far_end, 'start', lambda: None)
    assert not runtime.far_end.snapshot()['blocked']
    config = {'enabled': True, 'device': 'Soundcard:0', 'channels': 2, 'channel': 1,
              'threshold_db': -40, 'hold_ms': 800}
    response = await client.put('/api/far-end', json=config)
    assert response.status_code == 200
    assert response.json()['blocked']
    runtime.far_end.last_frame = time.monotonic()
    assert not runtime.far_end.snapshot()['blocked']
    runtime.far_end.active_until = time.monotonic() + 1
    assert runtime.far_end.snapshot()['blocked']
    runtime.far_end.last_frame = time.monotonic() - 2
    assert runtime.far_end.snapshot()['blocked']
    config['enabled'] = False
    assert (await client.put('/api/far-end', json=config)).status_code == 200
    assert not runtime.far_end.snapshot()['blocked']


async def test_paused_events_do_not_survive_resume(runtime):
    follow = runtime.follow
    old_engine = follow.engine
    runtime.far_end.settings['enabled'] = True
    follow.feed({'mic_id': 'primary', 'azimuth': 20, 'elevation': 30})
    assert follow.far_end_was_blocked and follow.engine is not old_engine
    blocked_engine = follow.engine
    runtime.far_end.settings['enabled'] = False
    assert not follow.far_end_blocked()
    assert follow.engine is not blocked_engine and follow.engine.current is None


def test_audio_frames_trigger_and_release_after_hold(tmp_path, monkeypatch):
    chunks = queue.Queue()
    stopped = threading.Event()
    class Output:
        def read(self, size):
            try:
                return chunks.get(timeout=.2)
            except queue.Empty:
                return b'' if stopped.is_set() else self.read(size)
    class FakeProcess:
        stdout = Output()
        def __init__(self, *a, **kw): pass
        def terminate(self): stopped.set()
        def wait(self, timeout=None): return 0
        def kill(self): stopped.set()
    monkeypatch.setattr('confdeck.far_end.inputs', lambda: [{'id': 'Soundcard:0'}])
    monkeypatch.setattr('confdeck.far_end.subprocess.Popen', FakeProcess)
    monitor = FarEndMonitor(tmp_path)
    monitor.configure({'enabled': True, 'device': 'Soundcard:0', 'channels': 1, 'channel': 1,
                       'threshold_db': -40, 'hold_ms': 100})
    def wait_for(condition):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            if condition(): return
            time.sleep(.01)
        raise AssertionError('Expected far-end state not reached')
    try:
        chunks.put(array.array('h', [10000] * 320).tobytes())
        wait_for(lambda: monitor.snapshot()['active'])
        assert monitor.snapshot()['blocked']
        time.sleep(.12)
        chunks.put(array.array('h', [0] * 320).tobytes())
        wait_for(lambda: monitor.snapshot()['healthy'] and not monitor.snapshot()['blocked'])
    finally:
        monitor.stop()
