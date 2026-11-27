"""Exercise the port-4000 editor against a temporary, simulated room."""
import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from playwright.async_api import async_playwright, expect

from confdeck.models import AdditionalMic, Settings
from confdeck.multimic import MultiMicFilter
from confdeck.tracking import Zone


async def main():
    settings = Settings(microphones=[AdditionalMic(id='rear', ip='192.168.1.93', port=45, protocol='tcc2')])
    settings.tracking.zones = [Zone(id='front', name='Front', azimuth=30, elevation=40),
                               Zone(id='rear-seat', name='Rear', mic_id='rear', azimuth=60, elevation=40)]
    tracking = MultiMicFilter(settings.tracking, 'legacy').snapshot(0)
    tracking.update(live=False, events=[])
    state = {'settings': settings.model_dump(), 'tracking': tracking,
             'mic': {'state':'Connected', 'age':.1, 'position':{'azimuth':30,'elevation':40,'level':-45}},
             'microphones':[{'id':'rear','state':'Connected','age':.1,'position':{'azimuth':60,'elevation':40,'level':-25}}],
             'speech': {'enabled':False}}
    app = FastAPI()
    writes = []
    script = Path('integration/current84/omnitrack-sennheiser/public/js/modules/sennheiser_module.js')

    @app.get('/')
    async def index():
        return HTMLResponse('<body class="omni-premium"><select id="ctrl-type"><option value="Sennheiser">Sennheiser</option></select><div id="page-mapping"><div class="scroll-area"><div id="sennheiser-mapping-panel"></div></div></div><script src="/editor.js"></script></body>')

    @app.get('/editor.js')
    async def editor():
        return Response(script.read_text(encoding='utf8'), media_type='text/javascript')

    @app.get('/api/sennheiser/state')
    async def get_state():
        return state

    @app.get('/api/check_dcerno_status')
    async def connected():
        return {'connected':True}

    @app.get('/api/get_mapping')
    async def mapping():
        return {'mapping':[]}

    @app.get('/api/get_layout_mode')
    async def mode():
        return {'mode':'auto'}

    @app.put('/api/sennheiser/microphones/{source}')
    async def update_mic(source: str, request: Request):
        writes.append((source, await request.json()))
        return {'id':source}

    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8878, log_level='error'))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(.05)
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            page = await browser.new_page(viewport={'width':1400,'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto('http://127.0.0.1:8878/')
            await expect(page.locator('[data-team-source="rear"]').first).to_be_visible()
            await expect(page.locator('.mic-team-card.loud')).to_contain_text('192.168.1.93')
            await page.locator('[data-team-toggle]').click()
            await expect(page.locator('[data-team-canvas]')).to_have_count(2)
            await page.locator('[data-team-seat="rear-seat"]').click()
            await expect(page.locator('[data-seat]')).to_have_value('rear-seat')
            assert await page.evaluate("window._sennheiserRadar.q('[data-seat-radar]').width") == 520
            await page.locator('.mic-team-connect summary').click()
            await page.locator('[data-team-config]').select_option('rear')
            await expect(page.locator('[data-team-protocol]')).to_have_value('tcc2')
            await page.locator('[data-team-connect]').click()
            for _ in range(30):
                if writes:
                    break
                await asyncio.sleep(.05)
            assert writes[0][0] == 'rear' and writes[0][1]['settings']['protocol'] == 'tcc2'
            assert not errors, errors
            await browser.close()
        print('PASS: port-4000 mixed maps, selected seat, live levels, TCC 2 configuration; no browser errors')
    finally:
        server.should_exit = True
        await task


if __name__ == '__main__':
    asyncio.run(main())
