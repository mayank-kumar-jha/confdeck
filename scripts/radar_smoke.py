"""Visual editor test; all config/device writes are mocked."""
import asyncio
import math

from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 1100})
        errors, saves, writes = [], [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        async def tracking(route):
            if route.request.method == "PUT":
                saves.append(route.request.post_data_json)
                await route.fulfill(json={"ok": True, "warnings": []})
            else:
                response = await route.fetch()
                data = await response.json()
                data["settings"]["zones"] = [{"id": "test-seat", "name": "Test seat", "azimuth": 90, "elevation": 45, "radius": 25, "scene_id": "", "presets": {}}]
                await route.fulfill(json=data)
        await page.route("**/api/tracking", tracking)
        await page.route("**/api/tracking/settings", tracking)
        async def device(route):
            if route.request.method == "POST":
                writes.append(route.request.post_data_json)
            await route.fulfill(json={"values": {"beam": {"sourceDetectionThreshold": "NormalRoom"}, "noise_gate": {"threshold": -80, "holdTime": 350}, "exclusion_0": {"enabled": True, "azimuth": {"min": 90, "max": 120}, "elevation": {"min": 20, "max": 40}}}, "snapshot_at": 1, "resources": {}, "unavailable": {}})
        await page.route("**/api/mic/device-settings", device)
        await page.goto("http://127.0.0.1:8765/#tune")
        await page.locator('[data-tab="zones"]').click()
        editor = page.locator('seat-radar')
        canvas = editor.locator('canvas')
        await canvas.wait_for()
        await expect(editor.locator('[data-add]')).to_be_disabled()
        await editor.locator('[data-lock]').click()
        await editor.locator('[data-seat]').select_option('test-seat')
        box = await canvas.bounding_box()
        def xy(x, y):
            return box['x'] + x * box['width'] / 520, box['y'] + y * box['height'] / 520
        await page.mouse.move(*xy(385, 260))
        await page.mouse.down()
        await page.mouse.move(*xy(380, 220), steps=6)
        await page.mouse.up()
        await expect(editor.locator('[data-note]')).to_have_text('Seats saved')
        assert saves[-1]['zones'][0]['azimuth'] != 90
        seat = saves[-1]['zones'][0]
        a = math.radians(seat['azimuth'] + seat['radius'])
        radius = 35 + 2 * seat['elevation']
        await page.mouse.move(*xy(260 + math.sin(a) * radius, 260 - math.cos(a) * radius))
        await page.mouse.down()
        a = math.radians(seat['azimuth'] + 40)
        await page.mouse.move(*xy(260 + math.sin(a) * radius, 260 - math.cos(a) * radius), steps=5)
        await page.mouse.up()
        await page.wait_for_timeout(800)
        assert saves[-1]['zones'][0]['radius'] > 35
        await editor.locator('[data-read]').click()
        await editor.locator('[data-sensitivity]').select_option('LoudRoom')
        await page.mouse.move(*xy(120, 120))
        await page.mouse.down()
        await page.mouse.move(*xy(130, 110), steps=4)
        await page.mouse.up()
        page.on('dialog', lambda dialog: dialog.accept())
        await editor.locator('[data-apply]').click()
        await expect(page.locator('#toast')).to_contain_text('verified')
        assert writes[-1]['changes']['beam'] == {'sourceDetectionThreshold': 'LoudRoom'}
        exclusion = writes[-1]['changes']['exclusion_0']
        assert exclusion['azimuth']['min'] != 90
        assert all(value % 5 == 0 for axis in ('azimuth', 'elevation') for value in exclusion[axis].values())
        await editor.locator('[data-lock]').click()
        await expect(editor.locator('[data-sensitivity]')).to_be_disabled()
        assert not errors, errors
        print('PASS: locked editor, seat drag/autosave, simple microphone apply/read-back; all writes mocked')
        await browser.close()


asyncio.run(main())
