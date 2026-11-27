"""Browser controls regression: intercept every outbound WS message; never move hardware."""
import asyncio

from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page()
        await page.add_init_script("""window.controlMessages=[];
            WebSocket.prototype.send=function(data){window.controlMessages.push(JSON.parse(data));};""")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto("http://127.0.0.1:8765/#cameras")
        pad = page.locator("camera-studio [data-pad]")
        await pad.wait_for()
        assert await pad.is_enabled()
        box = await pad.bounding_box()
        await page.mouse.move(box["x"] + box["width"] * .85, box["y"] + box["height"] / 2)
        await page.mouse.down()
        await page.wait_for_timeout(350)
        await page.mouse.up()
        messages = await page.evaluate("window.controlMessages.splice(0)")
        assert any(m["pan"] > .5 for m in messages), messages
        assert messages[-1]["pan"] == messages[-1]["tilt"] == messages[-1]["zoom"] == 0
        zoom = page.locator('camera-studio [data-zoom="1"]')
        await zoom.hover()
        await page.mouse.down()
        await page.wait_for_timeout(250)
        await page.mouse.up()
        messages = await page.evaluate("window.controlMessages.splice(0)")
        assert any(m["zoom"] == 1 for m in messages)
        assert messages[-1]["zoom"] == 0
        await pad.focus()
        await page.keyboard.down("ArrowLeft")
        await page.wait_for_timeout(250)
        await page.keyboard.up("ArrowLeft")
        messages = await page.evaluate("window.controlMessages.splice(0)")
        assert any(m["pan"] == -1 for m in messages)
        assert messages[-1]["pan"] == 0
        captured = []
        async def capture(route):
            captured.append(route.request.post_data_json)
            await route.fulfill(json={"ok": True})
        await page.route("**/api/scenes/capture", capture)
        await page.locator("camera-studio [data-name]").fill("Browser mock framing")
        await page.locator("camera-studio [data-create]").click()
        await expect(page.locator("[data-save-status]")).to_contain_text("saved")
        assert len(captured) == 1
        assert set(captured[0]) == {"camera", "name", "confirm_update"}
        assert await page.locator("camera-studio [data-slot], camera-studio [data-export], camera-studio [data-import]").count() == 0
        assert await page.locator('#camera-list [data-act="presets"]').count() == 0
        assert not errors, errors
        print("PASS: joystick, zoom, keyboard and release STOP; all outbound commands intercepted")
        await browser.close()


asyncio.run(main())
