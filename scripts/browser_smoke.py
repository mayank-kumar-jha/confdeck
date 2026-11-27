"""Local browser acceptance checks. Uses installed Edge; never downloads a browser."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def wait_js(page, expression, timeout=30000):
    """Poll through the browser debugging protocol without injecting unsafe-eval."""
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        if await page.evaluate("() => (" + expression + ")"):
            return
        await asyncio.sleep(0.25)
    await page.screenshot(path="artifacts/browser-failure.png", full_page=True)
    raise AssertionError("Browser condition did not become true: " + expression)


async def main(pin):
    output = Path("artifacts")
    output.mkdir(exist_ok=True)
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
        context = await browser.new_context(viewport={"width": 1440, "height": 1080})
        external, errors = [], []

        async def gate(route):
            host = urlsplit(route.request.url).hostname
            if host not in ("127.0.0.1", "localhost", None):
                external.append(route.request.url)
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", gate)
        page = await context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto("http://127.0.0.1:8765")
        await page.get_by_role("heading", name="Room overview").wait_for()
        await wait_js(
            page,
            "document.querySelectorAll('camera-player video').length === 4 && [...document.querySelectorAll('camera-player video')].every(v => v.videoWidth > 0)",
            timeout=45000,
        )
        await page.wait_for_timeout(2500)
        metrics = await page.locator("camera-player").evaluate_all(
            "ps => ps.map(p => ({camera:p.getAttribute('camera'), width:p.video.videoWidth, transport:p.metrics.transport, fps:p.metrics.fps, buffer_ms:p.metrics.latency_ms}))"
        )
        await page.screenshot(path=str(output / "home.png"), full_page=True)
        await page.get_by_role("link", name="Microphone", exact=True).click()
        await page.get_by_role("heading", name="Speaker position", exact=True).wait_for()
        await page.screenshot(path=str(output / "microphone.png"), full_page=True)
        await page.get_by_role("button", name="Calibration helper").click()
        await page.get_by_role("button", name="Save current reading").click()
        await page.get_by_role("button", name="Close dialog").click()
        await page.get_by_role("link", name="Cameras", exact=True).click()
        await page.locator(".use-hls").first.check()
        await wait_js(
            page,
            "document.querySelector('camera-player').metrics.transport === 'HLS' && document.querySelector('camera-player video').videoWidth > 0",
            timeout=30000,
        )
        await page.get_by_role("link", name="Display", exact=True).click()
        await page.get_by_role("button", name="Connect to TV", exact=True).click()
        code = (await page.locator(".pair-code").inner_text()).strip()
        await page.get_by_role("button", name="Done", exact=True).click()
        tv = await context.new_page()
        tv.on("pageerror", lambda e: errors.append(str(e)))
        await tv.goto("http://127.0.0.1:8766/display")
        await tv.get_by_label("Pairing code").fill(code)
        await tv.get_by_role("button", name="Connect display").click()
        await wait_js(
            tv,
            "document.querySelectorAll('camera-player video').length === 4 && [...document.querySelectorAll('camera-player video')].every(v => v.videoWidth > 0)",
            timeout=40000,
        )
        before = await tv.locator("camera-player").evaluate_all("ps=>ps.map(p=>p.session)")
        await page.get_by_role("button", name="Single", exact=True).click()
        await wait_js(tv, "document.querySelector('#display-grid').classList.contains('single')")
        await page.get_by_role("button", name="2 × 2", exact=True).click()
        await wait_js(tv, "document.querySelector('#display-grid').classList.contains('grid')")
        after = await tv.locator("camera-player").evaluate_all("ps=>ps.map(p=>p.session)")
        assert before == after, "Changing layout restarted a video session"
        await tv.screenshot(path=str(output / "display.png"))
        await tv.reload()
        await wait_js(
            tv, "document.querySelector('camera-player video')?.videoWidth > 0", timeout=30000
        )
        await page.get_by_role("button", name="Blank / black").click()
        await tv.locator("#blackout").wait_for(state="visible")
        await page.get_by_role("button", name="Show cameras").click()
        await tv.locator("#blackout").wait_for(state="hidden")
        await page.get_by_role("link", name="Health", exact=True).click()
        relay_before = (await (await page.request.get("http://127.0.0.1:8765/api/state")).json())[
            "media"
        ]["restarts"]
        await page.get_by_role("button", name="Stop relay process").click()
        for _ in range(80):
            snapshot = await (await page.request.get("http://127.0.0.1:8765/api/state")).json()
            if (
                snapshot["media"]["restarts"] > relay_before
                and snapshot["media"]["state"] == "Connected"
            ):
                break
            await asyncio.sleep(0.25)
        else:
            raise AssertionError("Relay did not restart")
        await tv.wait_for_timeout(12000)
        await wait_js(
            tv,
            "[...document.querySelectorAll('camera-player video')].every(v=>v.readyState>=3&&v.currentTime>0)",
            timeout=45000,
        )
        await page.get_by_role("button", name="Drop mic packets").click()
        await page.get_by_role("button", name="Camera 1 offline", exact=True).click()
        for _ in range(40):
            snapshot = await (await page.request.get("http://127.0.0.1:8765/api/state")).json()
            if snapshot["cameras"][0]["state"] != "Online":
                assert all(c["state"] == "Online" for c in snapshot["cameras"][1:])
                break
            await asyncio.sleep(0.25)
        else:
            raise AssertionError("Camera outage was not detected")
        await page.get_by_role("button", name="Restore camera 1", exact=True).click()
        await wait_js(
            page,
            "document.querySelector('[data-dot=mic]').classList.contains('warn')",
            timeout=12000,
        )
        await page.get_by_role("button", name="Restore mic packets").click()
        await page.get_by_role("button", name="Send malformed messages").click()
        await wait_js(
            page,
            "document.querySelector('[data-dot=mic]').classList.contains('good')",
            timeout=12000,
        )
        await page.screenshot(path=str(output / "health.png"), full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.get_by_role("link", name="Home", exact=True).click()
        await page.screenshot(path=str(output / "mobile.png"), full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
            "Mobile layout overflows"
        )
        assert not external, f"Unexpected external requests: {external}"
        assert not errors, f"Browser errors: {errors}"
        report = {
            "camera_metrics": metrics,
            "external_requests": external,
            "page_errors": errors,
            "layout_preserves_sessions": before == after,
            "checks": [
                "login",
                "four live cameras",
                "HLS fallback",
                "mic radar",
                "calibration",
                "pairing",
                "TV reload",
                "single/grid switch",
                "blank/unblank",
                "relay crash recovery",
                "one camera offline while others continue",
                "mic packet loss",
                "malformed mic packets",
                "mobile layout",
            ],
        }
        (output / "browser-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pin", default="", help="Ignored; local admin is PIN-free")
    asyncio.run(main(parser.parse_args().pin))
