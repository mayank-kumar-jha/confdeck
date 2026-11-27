"""Hardware-free browser check for the seat sample review flow."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect

from confdeck.tracking import TrackingSettings


async def main():
    state = {'settings': {'tracking': TrackingSettings().model_dump(), 'cameras': [], 'microphones': []},
             'tracking': {'reason': 'waiting', 'posteriors': {}},
             'accuracy': {'recording': False, 'elapsed': 0, 'dropped': 0, 'ab': {'running': False}}}
    sample = {'status': 'idle'}
    calls, errors = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        page = await browser.new_page(viewport={'width': 1100, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def route(request):
            nonlocal sample
            path = request.request.url.split('127.0.0.1:9999')[-1]
            if path == '/':
                html = '<link rel="stylesheet" href="/static/style.css"><script type="module">'
                html += "import {room} from '/static/common.js'; room.state=" + json.dumps(state) + ';'
                html += "await import('/static/accuracy-lab.js'); document.body.append(document.createElement('accuracy-lab'));</script>"
                return await request.fulfill(content_type='text/html', body=html)
            if path.startswith('/static/'):
                asset = Path('confdeck/static') / path.split('/')[-1]
                return await request.fulfill(path=str(asset.resolve())) if asset.is_file() else await request.fulfill(status=404)
            if path == '/api/accuracy/seat-sample':
                if request.request.method == 'POST':
                    body = request.request.post_data_json
                    calls.append(body['action'])
                    if body['action'] == 'start':
                        sample = {'status': 'ready', 'name': body['name'], 'elapsed': 20,
                                  'proposal': {'id': 'chair', 'name': body['name'], 'mic_id': 'primary', 'azimuth': 30, 'elevation': 40, 'radius': 25},
                                  'quality': {'readings': 80, 'seconds_with_readings': 20, 'direction_support': .95, 'spread_90_degrees': 3, 'median_level': -40},
                                  'warnings': ['Verify with a separate test.']}
                        if body['mic_id'] == '__all__':
                            sample['proposal']['microphone_profiles'] = {
                                'rear': {'azimuth': 210, 'elevation': 35, 'radius': 25}}
                            sample['per_microphone'] = {'primary': sample['quality'], 'rear': sample['quality']}
                    elif body['action'] == 'save':
                        proposal, sample = sample['proposal'], {'status': 'idle'}
                        return await request.fulfill(json={'saved': proposal})
                    elif body['action'] == 'cancel':
                        sample = {'status': 'idle'}
                return await request.fulfill(json=sample)
            return await request.fulfill(json={})

        await page.route('**/*', route)
        await page.goto('http://127.0.0.1:9999/')
        await expect(page.locator('[data-action="sample-save"]')).to_be_disabled()
        await page.locator('[data-sample-name]').fill('Chair')
        await page.locator('[data-action="sample-start"]').click()
        await expect(page.locator('[data-sample-detail]')).to_contain_text('95%')
        await expect(page.locator('[data-sample-warning]')).to_contain_text('separate test')
        await expect(page.locator('[data-action="sample-save"]')).to_be_enabled()
        await page.locator('[data-action="sample-save"]').click()
        await expect(page.locator('[data-sample-status]')).to_contain_text('Chair saved')
        assert calls == ['start', 'save']
        state['settings']['microphones'] = [{'id': 'rear'}]
        await page.goto('http://127.0.0.1:9999/')
        await expect(page.locator('[data-sample-mic]')).to_have_value('__all__')
        await page.locator('[data-sample-name]').fill('Shared chair')
        await page.locator('[data-action="sample-start"]').click()
        await expect(page.locator('[data-sample-detail]')).to_contain_text('primary:')
        await expect(page.locator('[data-sample-detail]')).to_contain_text('rear:')
        await page.locator('[data-action="sample-save"]').click()
        await expect(page.locator('[data-sample-status]')).to_contain_text('Shared chair saved')
        assert calls == ['start', 'save', 'start', 'save']
        assert not errors, errors
        await browser.close()
        print('Seat sample preview and explicit save passed in the browser.')


if __name__ == '__main__':
    asyncio.run(main())
