import asyncio
import copy

import httpx
import pytest

from confdeck.device_settings import DeviceSettings
from confdeck.microphone import MockSSCv2Server, SSCv2Adapter
from confdeck.models import MicSettings


def defaults(schema):
    if schema.get('type') == 'object':
        return {k: defaults(v) for k, v in schema['properties'].items()}
    if 'example' in schema:
        return schema['example']
    if 'default' in schema:
        return schema['default']
    if 'enum' in schema:
        return schema['enum'][0]
    if schema['type'] == 'boolean':
        return False
    return schema.get('minimum', 0)


async def test_snapshot_apply_readback_revert_over_pinned_https(runtime):
    server = MockSSCv2Server()
    for resource in runtime.device_settings.resources.values():
        server.settings_values[resource['path']] = defaults(resource['schema'])
    original = copy.deepcopy(server.settings_values)
    port = await server.start()
    settings = MicSettings(ip='127.0.0.1', port=port, enabled=True, tls_fingerprint=server.fingerprint)
    runtime.settings.mic = settings
    runtime.mic = SSCv2Adapter(settings, 'test-password')
    await runtime.mic.connect()
    try:
        for _ in range(100):
            if runtime.mic.state == 'Connected':
                break
            await asyncio.sleep(.02)
        service = runtime.device_settings
        with pytest.raises(ValueError, match='confirm'):
            await service.apply({'noise_gate': {'threshold': -60}})
        result = await service.apply({'noise_gate': {'threshold': -60, 'holdTime': 500}}, True)
        assert result['values']['noise_gate']['threshold'] == -60
        assert result['snapshot_at']
        # A new service instance uses the same durable snapshot, not the latest values.
        service = DeviceSettings(runtime)
        await service.apply({'noise_gate': {'threshold': -50}}, True)
        await service.apply({}, True, revert=True)
        assert server.settings_values == original
        with pytest.raises(ValueError, match='range or step'):
            await service.apply({'noise_gate': {'holdTime': 51}}, True)
        server.error_mode = 'auth_fail'
        with pytest.raises(ValueError, match='password'):
            await service.request('GET', 'noise_gate')
    finally:
        await runtime.mic.close()
        runtime.mic = None
        await server.stop()


async def test_incomplete_snapshot_blocks_every_write(runtime, monkeypatch):
    service = runtime.device_settings
    writes = []

    async def request(method, key, value=None):
        if method != 'GET':
            writes.append(key)
        if key == 'priority_0':
            raise ValueError('Unsupported firmware')
        return defaults(service.resources[key]['schema'])

    monkeypatch.setattr(service, 'request', request)
    with pytest.raises(ValueError, match='Snapshot incomplete'):
        await service.apply({'noise_gate': {'threshold': -60}}, True)
    assert writes == []
    assert not runtime.store.db.execute('SELECT * FROM mic_snapshots').fetchall()


@pytest.mark.parametrize('status,phrase', [(401,'password'), (403,'permissions'), (404,'unavailable'), (422,'value')])
async def test_device_error_messages(runtime, status, phrase):
    class Adapter:
        async def resource_request(self, method, path, **kwargs):
            response = httpx.Response(status, request=httpx.Request(method, 'https://127.0.0.1' + path))
            response.raise_for_status()

    runtime.mic = Adapter()
    try:
        with pytest.raises(ValueError, match=phrase):
            await runtime.device_settings.request('GET', 'noise_gate')
    finally:
        runtime.mic = None


async def test_readback_mismatch_keeps_original_snapshot(runtime, monkeypatch):
    service = runtime.device_settings

    async def request(method, key, value=None):
        return defaults(service.resources[key]['schema'])  # simulate an ignored write

    monkeypatch.setattr(service, 'request', request)
    with pytest.raises(ValueError, match='Read-back did not match'):
        await service.apply({'noise_gate': {'threshold': -60}}, True)
    assert runtime.store.db.execute('SELECT count(*) FROM mic_snapshots').fetchone()[0] == 1
