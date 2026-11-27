"""Isolated browser acceptance: synthetic microphones, no room hardware writes."""
import asyncio
import tempfile
from pathlib import Path

import uvicorn
from playwright.async_api import async_playwright, expect

from confdeck.app import create_app
from confdeck.models import Camera
from confdeck.runtime import Runtime
from confdeck.tracking import Zone


async def main():
    with tempfile.TemporaryDirectory(prefix="confdeck-multimic-") as folder:
        runtime = Runtime(Path(folder), demo=True)
        runtime.settings.preview_limit = 2
        runtime.settings.cameras = [Camera(id=f"cam{i}", ip=f"192.168.99.{i+10}") for i in range(6)]
        runtime.settings.tracking.zones = [Zone(id="original", name="Original seat", azimuth=90, elevation=45)]
        server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=8877, log_level="error"))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(.05)
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel="msedge", headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.route("**/media/**", lambda r: r.fulfill(status=503, body="Mock video"))
                await page.goto("http://127.0.0.1:8877/#home")
                await expect(page.locator("#home-cameras camera-player")).to_have_count(2)
                await page.locator('[data-nav="seats"]').click()
                await expect(page.locator("#mic-select")).to_have_count(0)
                await page.locator('[data-act="add-mic"]').click()
                await page.locator('#mic-form [name="protocol"]').select_option('tcc2')
                await expect(page.locator('#mic-form [name="port"]')).to_have_value('45')
                await page.locator('#mic-form [name="ip"]').fill("192.168.99.93")
                await page.locator('#mic-form [name="password"]').fill("mock-password")
                await page.locator('#mic-form button[type="submit"]').click()
                await expect(page.locator('seat-radar [data-lock]')).to_be_visible()
                await expect(page.locator('mic-team .mic-team-card')).to_have_count(2)
                await page.locator('mic-team [data-mixed]').click()
                await expect(page.locator('mic-team canvas')).to_have_count(2)
                identity = runtime.settings.microphones[0].id
                await expect(page.locator("#mic-select")).to_have_value(identity)
                await expect(page.locator('seat-radar [data-seat] option')).to_have_count(1)
                await page.locator('seat-radar [data-lock]').click()
                await page.locator('seat-radar [data-add]').click()
                await page.locator('seat-radar [data-save]').click()
                await expect(page.locator('seat-radar [data-note]')).to_contain_text('Seats saved')
                assert {z.mic_id for z in runtime.settings.tracking.zones} == {"primary", identity}
                await page.select_option("#mic-select", "primary")
                await expect(page.locator('seat-radar [data-seat] option')).to_have_count(2)
                await expect(page.locator('seat-radar [data-seat] option[value="original"]')).to_have_text("Original seat")
                await page.select_option("#mic-select", identity)
                await page.locator('[data-nav="tuning"]').click()
                await expect(page.locator("mic-tuning")).to_contain_text("192.168.99.93")
                seen = []

                async def device(route):
                    seen.append(route.request.url)
                    await route.fulfill(json={"values": {}, "unavailable": {}, "resources": {}, "snapshot_at": None})

                await page.route("**/api/mic/device-settings?*", device)
                await page.locator('[data-read]').click()
                assert seen and seen[-1].endswith("source="+identity)
                await page.locator('[data-nav="display"]').click()
                await expect(page.locator('#program-preview')).to_be_visible()
                await expect(page.locator('smart-preview')).to_be_visible()
                await page.locator('[data-nav="seats"]').click()
                await expect(page.locator('mic-team canvas')).to_have_count(2)
                await page.locator('mic-team [data-team-seat="original"]').click()
                await expect(page.locator('#mic-select')).to_have_value('primary')
                await expect(page.locator('seat-radar [data-seat]')).to_have_value('original')
                out = Path("artifacts/multimic")
                out.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(out / "setup.png"), full_page=True)
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
                await page.screenshot(path=str(out / "mobile.png"), full_page=True)
                assert not errors, errors
                await browser.close()
                print("PASS: add mic, isolated seat maps, selected-device tuning, preview cap, mobile layout; no browser errors")
        finally:
            server.should_exit = True
            await task
            await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
