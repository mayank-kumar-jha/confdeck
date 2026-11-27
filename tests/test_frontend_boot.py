"""Check the real HTTP asset allowlist against the entry module's dependencies."""
import posixpath
import re

import httpx

from confdeck.app import create_app


async def test_all_admin_module_dependencies_are_served(client):
    pending, visited = ['/static/app.js'], set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        response = await client.get(path)
        assert response.status_code == 200, path
        assert 'javascript' in response.headers['content-type'], path
        for dependency in re.findall(r"(?:import\s+(?:[^;\n]*?\s+from\s+)?)[\"'](\.[^\"']+)[\"']", response.text):
            pending.append(posixpath.normpath(posixpath.join(posixpath.dirname(path), dependency)))
        for dependency in re.findall(r"import\(\s*[\"'](\.[^\"']+)[\"']", response.text):
            pending.append(posixpath.normpath(posixpath.join(posixpath.dirname(path), dependency)))
    assert '/static/smart-preview.js' in visited
    assert '/static/mic-tuning.js' in visited


async def test_smart_preview_remains_admin_only(runtime):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime, display=True)), base_url='http://127.0.0.1') as client:
        assert (await client.get('/static/smart-preview.js')).status_code == 404
