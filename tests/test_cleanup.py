import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from confdeck.errors import classify_camera
from confdeck.models import Camera, Settings
from confdeck.store import Store
from confdeck.tracking import TrackingSettings
from confdeck.tuning_preview import preview


def test_removed_settings_migrate_without_losing_seats(tmp_path):
    store = Store(tmp_path)
    data = Settings().model_dump()
    data['audio'] = {'enabled': True, 'stt': True, 'device': 99}
    data['display']['captions'] = True
    data['room_name'] = 'Preserved room'
    result = store._load_settings(json.dumps(data))
    assert result.room_name == 'Preserved room'
    assert 'audio' not in result.model_dump()
    assert 'captions' not in result.display.model_dump()
    assert 'audio' not in json.loads(store.backup.read_text())
    store.close()


async def test_removed_routes_and_shared_state(client):
    for path in ('/api/audio', '/api/audio/models', '/api/captions.srt'):
        assert (await client.post(path, json={})).status_code in (404, 405)
    state = (await client.get('/api/state')).json()
    assert 'audio' not in state
    assert 'accuracy' in state
    assert 'event_stats' in state['tracking']


def test_what_if_gate_changes_results_without_mutation():
    settings = TrackingSettings()
    rows = [{'t': i/10, 'event': {'azimuth': 90, 'elevation': 45, 'level': -65, 'time': i/10}} for i in range(20)]
    original = settings.model_dump()
    accepted = preview(rows, settings)
    proposal = settings.model_copy(update={'level_gate': True, 'level_threshold': -50})
    rejected = preview(rows, proposal)
    assert accepted['accepted'] == 20
    assert rejected['rejected'] == 20
    assert all('silence' in p['reason'] for p in rejected['events'])
    assert settings.model_dump() == original


async def test_what_if_never_dispatches(runtime, client):
    runtime.follow.dispatch = AsyncMock()
    response = await client.post('/api/tracking/preview', json=runtime.settings.tracking.model_dump())
    assert response.status_code == 200
    assert response.json()['events'] == []
    runtime.follow.dispatch.assert_not_called()


@pytest.mark.parametrize('message,code', [('503 busy','limit'),('453','limit'),('connection reset','limit'),('refused','limit'),('timed out','limit'),('401','password'),('404','path')])
def test_busy_auth_path_errors(message, code):
    assert classify_camera(message) == code


@pytest.mark.parametrize('info,state', [
    ({'codec':'h264','frames':1}, 'Online'),
    ({'codec':'h264','frames':0}, 'Waiting for keyframe'),
    ({'codec':'hevc','frames':1}, 'Unsupported codec'),
    ({'codec':None,'error':'password'}, 'Wrong password'),
    ({'codec':None,'error':'path'}, 'Stream path was not found'),
    ({'codec':None,'error':'limit'}, 'Camera is busy'),
])
async def test_probe_reads_relay_only(runtime, monkeypatch, info, state):
    calls = []
    async def probe(_tool,url,transport='tcp'):
        calls.append((url,transport))
        return info
    monkeypatch.setattr('confdeck.media._probe_stream_direct', probe)
    monkeypatch.setattr('confdeck.media.binary', lambda _: 'ffprobe')
    runtime.media.configure = AsyncMock()
    camera = Camera(ip='192.168.99.20',path='/live',model='AVer')
    result = await runtime.media.probe(camera)
    assert result['state'] == state
    assert calls == [(f'rtsp://127.0.0.1:8554/{camera.id}', 'tcp')]


async def test_canon_fallback_serial_and_udp(runtime, monkeypatch):
    active = []
    concurrent = 0
    async def configure(camera, credentials):
        active.append((camera.path, runtime.media.transports[camera.id]))
    async def probe(_tool, url, transport='tcp'):
        nonlocal concurrent
        concurrent += 1
        assert concurrent == 1
        await asyncio.sleep(0)
        concurrent -= 1
        assert url.startswith('rtsp://127.0.0.1:8554/')
        if active[-1] == ('/rtpstream/config2', 'udp'):
            return {'codec':'h264','frames':1}
        return {'codec':None,'timed_out':True}
    runtime.media.configure = configure
    monkeypatch.setattr('confdeck.media._probe_stream_direct',probe)
    monkeypatch.setattr('confdeck.media.binary',lambda _: 'ffprobe')
    camera = Camera(ip='192.168.99.20',path='/rtpstream/config1')
    result = await runtime.media.probe(camera)
    assert result['state'] == 'Online'
    assert camera.path == '/rtpstream/config2'
    assert active == [('/rtpstream/config1','tcp'),('/rtpstream/config1','udp'),('/rtpstream/config2','tcp'),('/rtpstream/config2','udp')]


async def test_running_relay_diagnosis_never_describes_upstream(runtime, monkeypatch):
    runtime.media.process = SimpleNamespace(returncode=0)
    runtime.media.probe = AsyncMock(return_value={'state': 'Wrong password', 'issue': {'code': 'password'}})
    direct = AsyncMock(side_effect=AssertionError('Extra upstream connection'))
    monkeypatch.setattr('confdeck.camera_diagnostics.describe', direct)
    monkeypatch.setattr('confdeck.camera_diagnostics.tcp_open', direct)
    runtime.media.process.returncode = None
    try:
        report = await runtime.diagnostics.video(Camera(ip='192.168.99.20'))
        assert report['failure']['code'] == 'auth'
        assert len(report['stages']) == 1
        direct.assert_not_called()
    finally:
        runtime.media.process = None


@pytest.mark.parametrize('status', [401, 404, 453, 503])
async def test_mock_rtsp_describe_status(status):
    from confdeck.camera_diagnostics import describe
    async def reply(reader, writer):
        try:
            await reader.readuntil(b'\r\n\r\n')
            writer.write(f'RTSP/1.0 {status} Failure\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n'.encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(reply, '127.0.0.1', 0)
    async with server:
        camera = Camera(ip='127.0.0.1', port=server.sockets[0].getsockname()[1])
        assert (await describe(camera, {}))['status'] == status


async def test_relay_ready_hevc_is_not_playable():
    from confdeck.media import Media
    media = SimpleNamespace(
        api=AsyncMock(return_value={"items": [{"name": "cam", "ready": True,
                         "tracks": ["H265"], "bytesReceived": 1000}]}),
        process=None, statuses={}, last_errors={})
    await Media.refresh(media)
    assert media.statuses["cam"]["state"] == "Unsupported codec"
    assert media.statuses["cam"]["codec"] == "hevc"
