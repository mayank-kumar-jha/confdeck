"""Isolated UI verification: no room hardware or saved settings are touched."""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import uvicorn
from playwright.async_api import async_playwright, expect

from confdeck.app import create_app
from confdeck.models import Camera
from confdeck.runtime import Runtime
from confdeck.tracking import Zone


async def main():
    with tempfile.TemporaryDirectory(prefix="confdeck-cleanup-") as folder:
        runtime = Runtime(Path(folder))
        runtime.settings.wizard_done = True
        runtime.settings.cameras = [Camera(id=f"cam{i}", name=f"Camera {i}", ip=f"192.168.99.{i+10}") for i in range(3)]
        runtime.settings.cameras[0].control_enabled = True
        runtime.settings.cameras[0].control_protocol = "visca"
        runtime.manual.accept = AsyncMock()
        runtime.settings.tracking.zones = [Zone(id="seat1", name="Seat One", azimuth=90, elevation=40)]
        server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=8875, log_level="error"))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(.05)
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="msedge", headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 1050})
                errors, sockets = [], []
                page.on("pageerror", lambda error: errors.append(str(error)+" "+error.stack))
                page.on("websocket", lambda ws: sockets.append(ws.url))
                await page.route("**/media/**", lambda route: route.fulfill(status=503, body="Mock camera"))
                await page.route("**/api/cameras/**", lambda route: route.fulfill(json={"ok": True}))
                device_values = {"beam": {"sourceDetectionThreshold": "NormalRoom"}, "noise_gate": {"threshold": -80, "holdTime": 350}, "exclusion_0": {"enabled": True, "azimuth": {"min": 90, "max": 120}, "elevation": {"min": 20, "max": 40}}}
                device_writes = []
                async def device_route(route):
                    if route.request.method == "POST":
                        body = route.request.post_data_json
                        assert body['confirm'] is True
                        device_writes.append(body['changes'])
                        for key, value in body['changes'].items():
                            device_values[key].update(value)
                    await route.fulfill(json={"values":device_values,"snapshot_at":1,"resources":{},"unavailable":{}})
                await page.route("**/api/mic/device-settings",device_route)
                page.on('dialog', lambda dialog: dialog.accept())
                await page.goto("http://127.0.0.1:8875/#home")
                await page.wait_for_timeout(1500)
                await page.locator('.nav a').last.wait_for(timeout=5000)
                assert await page.locator('.nav a').all_text_contents() == ['Overview', 'Cameras', 'Display', 'Seats and Mic', 'Mic Tuning', 'Advanced']
                for tab in ('home', 'cameras', 'seats', 'tuning', 'advanced', 'home'):
                    await page.locator(f'[data-nav="{tab}"]').click()
                    await page.wait_for_timeout(350)
                    if tab == 'cameras':
                        await page.locator('[data-pad]').focus()
                        await page.keyboard.down('ArrowRight')
                        await page.wait_for_timeout(250)
                        await page.keyboard.up('ArrowRight')
                        await page.wait_for_timeout(100)
                        commands=[call.args[1] for call in runtime.manual.accept.call_args_list]
                        assert any(c.get('pan',0)>0 for c in commands), commands
                        assert commands[-1]['pan'] == 0, commands
                    if tab == 'tuning':
                        await page.locator('[data-preview]').click()
                        await expect(page.locator('[data-result]')).to_contain_text('seconds available')
                        await page.locator('[data-read]').click()
                        await page.locator('[data-lock]').click()
                        await page.locator('[data-sensitivity]').select_option('QuietRoom')
                        assert not device_writes
                        await page.locator('[data-apply]').click()
                        await expect(page.locator('[data-comparison]')).to_contain_text('Collecting after')
                        assert device_writes[-1]['beam']['sourceDetectionThreshold'] == 'QuietRoom'
                    if tab == 'advanced':
                        await page.get_by_text('Tracking filter and camera control', exact=True).click()
                        await page.locator('[data-profile="Calm"]').click()
                        await expect(page.locator('[data-filter="confirm_ms"]')).to_have_value('1500')
                    if tab == 'home':
                        assert await page.locator('#home-cameras camera-player').count() == 3
                assert len(sockets) == 1, sockets
                assert not errors, errors
                out = Path('artifacts/cleanup')
                out.mkdir(parents=True, exist_ok=True)
                assert await page.locator('#program-preview').evaluate("el=>getComputedStyle(el).position") == 'relative'
                assert await page.locator('h1').first.evaluate("el=>getComputedStyle(el).color") == 'rgb(32, 50, 71)'
                await page.screenshot(path=str(out/'overview.png'), full_page=True)
                await page.locator('[data-nav="tuning"]').click()
                await page.screenshot(path=str(out/'tuning.png'), full_page=True)
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                await browser.close()
                print('PASS: five tabs, one WebSocket, isolated replay, all camera tiles, mobile width, no page errors')
        finally:
            server.should_exit = True
            await task
            await runtime.close()


if __name__ == '__main__':
    asyncio.run(main())
