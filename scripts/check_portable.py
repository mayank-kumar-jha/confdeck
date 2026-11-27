"""Verify the embedded runtime with outbound networking blocked, without room hardware."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

import confdeck
from confdeck.app import create_app
from confdeck.offline import install_guard
from confdeck.runtime import Runtime


async def main():
    root = Path(__file__).resolve().parents[1]
    assert (root / 'bundle' / 'ConfDeck-Windows' / 'runtime') in Path(sys.executable).parents
    assert 'site-packages' in confdeck.__file__
    install_guard()
    with tempfile.TemporaryDirectory(dir=root / '.runtime', prefix='portable-check-') as folder:
        assert Path(folder).resolve().is_relative_to((root / '.runtime').resolve())
        runtime = Runtime(Path(folder), demo=True)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)),base_url='http://127.0.0.1',headers={'x-confdeck':'1'}) as client:
                assert (await client.post('/api/login',json={'pin':runtime.auth.pin})).status_code == 200
                for path in ('/','/api/state','/static/app.js','/static/vendor/hls.min.js','/static/vendor/inter.woff2'):
                    response = await client.get(path)
                    assert response.status_code == 200, path
                print(json.dumps({'python':sys.version.split()[0],'offline_assets':True,'network_guard':True},indent=2))
        finally:
            await runtime.close()


if __name__=='__main__':
    asyncio.run(main())
