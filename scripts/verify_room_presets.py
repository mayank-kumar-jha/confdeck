"""Read-only browser QA against the configured room; never invokes preset commands."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    Path("artifacts").mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="msedge",
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
            ],
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto("http://127.0.0.1:8765/#cameras")
        await page.locator('[data-act="presets"][data-id="b1af941542af"]').wait_for()
        await page.locator('[data-act="presets"][data-id="b1af941542af"]').click()
        await page.get_by_text("Saved on camera", exact=True).wait_for()
        print(
            "Canon preset buttons:",
            await page.locator('[data-act="preset-recall"]').count(),
            flush=True,
        )
        await page.screenshot(path="artifacts/canon-presets.png", full_page=True)
        await page.locator('[data-act="close-dialog"]').click()
        width = 0
        for _ in range(100):
            width = await page.evaluate(
                "() => document.querySelector('camera-player[camera=\"b1af941542af\"] video')?.videoWidth || 0"
            )
            if width:
                break
            await asyncio.sleep(0.3)
        print("Canon video width:", width, flush=True)
        print("JS errors:", json.dumps(errors), flush=True)
        await page.screenshot(path="artifacts/room-cameras.png", full_page=True)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
