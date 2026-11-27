"""Offline browser smoke: every request is intercepted; no hardware or live server."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, expect

from confdeck.evaluation.synthetic import generate


async def main():
    _, settings = generate()
    settings.zones[0].presets = {}
    settings.zones[1].presets = {}
    calls, errors = [], []
    recording = False
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        page = await browser.new_page(viewport={'width': 1300, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        async def route(request):
            nonlocal recording
            path = request.request.url.split('127.0.0.1:9999')[-1]
            if path == '/':
                return await request.fulfill(content_type='text/html', body='<link rel="stylesheet" href="/static/style.css"><accuracy-lab></accuracy-lab><script type="module" src="/static/accuracy-lab.js"></script>')
            if path.startswith('/static/'):
                asset = Path('confdeck/static') / path.split('/')[-1]
                if asset.is_file():
                    return await request.fulfill(path=str(asset.resolve()))
                return await request.fulfill(status=404)
            calls.append(path)
            if path == '/api/state':
                return await request.fulfill(json={'settings': {'tracking': settings.model_dump(), 'cameras': []}})
            if path == '/api/accuracy/session':
                body = request.request.post_data_json if request.request.method == 'POST' else {}
                recording = body.get('action') == 'start'
                return await request.fulfill(json={})
            if path == '/api/accuracy/status':
                return await request.fulfill(json={'recording': recording, 'elapsed': 0., 'dropped': 0, 'ab': {'running': False}, 'tracking': {'current': 'a', 'reason': 'accepted', 'posteriors': {'a': .9, 'none': .1}, 'history': []}})
            return await request.fulfill(json={})
        await page.route('**/*', route)
        await page.goto('http://127.0.0.1:9999/')
        await expect(page.locator('h1')).to_have_text('Accuracy Lab')
        await page.locator('[data-action="start"]').click()
        await expect(page.locator('[data-session]')).to_contain_text('Recording')
        await page.locator('[data-action="stop"]').click()
        await expect(page.locator('[data-session]')).to_contain_text('Stopped')
        assert not any('/vision/frame' in path for path in calls)
        assert not errors, errors
        print('Accuracy Lab rendered; session controls work; vision Off sends no frames; no JS errors.')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
