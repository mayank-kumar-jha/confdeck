"""Offline UI checks: all requests intercepted, no camera connections or movements."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from confdeck.models import Camera, Settings
from confdeck.tracking import Zone


async def main():
    settings = Settings(cameras=[Camera(id=f'c{i}', name=f'Camera {i}', ip=f'127.0.0.{i}') for i in range(1,4)])
    settings.tracking.zones = [Zone(id='presenter', name='Presenter', azimuth=0, elevation=30, presets={'c3':'21'})]
    settings.tracking.conversation_layout = True
    settings.display.cameras = ['c3','c1','c2']
    settings.display.layout = 'three'
    state = {'settings':settings.model_dump(), 'tracking':{'live':True}}
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        page = await browser.new_page(viewport={'width':1200,'height':1000})
        errors, saved = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        async def route(request):
            path=request.request.url.split('http://127.0.0.1:9999')[-1]
            if path=='/':
                return await request.fulfill(content_type='text/html',body='''<link rel="stylesheet" href="/static/style.css"><div id="toast"></div><script type="module">
                    await import('/static/player.js');
                    customElements.get('camera-player').prototype.start=async function(){};
                    await import('/static/smart-preview.js');
                    document.body.append(document.createElement('smart-preview'));
                    </script>''')
            if path.startswith('/static/'):
                asset = Path('confdeck/static') / path.removeprefix('/static/')
                if asset.is_file():
                    return await request.fulfill(path=str(asset.resolve()))
                return await request.fulfill(status=404)
            if path=='/api/state':
                return await request.fulfill(json=state)
            if path=='/api/tracking/conversation':
                saved.append(request.request.post_data_json)
                return await request.fulfill(json={'ok':True})
            return await request.fulfill(status=404)
        await page.route('**/*',route)
        await page.goto('http://127.0.0.1:9999/')
        await page.locator('[data-smart-save]').wait_for()
        assert await page.locator('camera-player').count()==3
        assert await page.locator('[data-smart-video]').evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == 2
        # Reorder then collapse: CSS must select slot order, not DOM insertion order.
        state['settings']['display'].update(cameras=['c1','c3'],layout='two')
        await page.evaluate("s=>document.querySelector('smart-preview').update(s)",state)
        assert await page.locator('camera-player').count()==2
        state['settings']['display'].update(cameras=['c1'],layout='single')
        await page.evaluate("s=>document.querySelector('smart-preview').update(s)",state)
        assert await page.locator('camera-player[camera="c1"]').is_visible()
        await page.select_option('[data-presenter-seat]','presenter')
        await page.select_option('[data-presenter-camera]','c3')
        await page.click('[data-smart-save]')
        assert saved==[{'conversation_layout':True,'presenter_seat':'presenter','presenter_camera':'c3'}]
        assert await page.locator('[data-speed-test],.codec-table').count()==0
        assert not errors,errors
        await browser.close()
        print('Smart preview 3/2/1 layouts, presenter save, clean UI: passed; all traffic mocked.')


if __name__=='__main__':
    asyncio.run(main())
