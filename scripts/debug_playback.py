"""Print local browser playback diagnostics without opening an external page."""

import argparse
import asyncio

from playwright.async_api import async_playwright


async def main(pin):
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="msedge", headless=True)
        page = await b.new_page()
        page.on("console", lambda m: print("CONSOLE", m.type, m.text[:500], flush=True))
        page.on("pageerror", lambda e: print("ERROR", e, flush=True))
        page.on(
            "response",
            lambda r: print("HTTP", r.status, r.url, flush=True) if "/media/" in r.url else None,
        )
        await page.goto("http://127.0.0.1:8765")
        print(
            await page.evaluate(
                "() => ({hlsType:typeof Hls,hlsSupport:window.Hls?.isSupported(),mediaSource:typeof MediaSource,avc:MediaSource.isTypeSupported('video/mp4; codecs=\"avc1.42E01E\"'),nativeHls:document.createElement('video').canPlayType('application/vnd.apple.mpegurl')})"
            ),
            flush=True,
        )
        await page.get_by_label("Startup PIN").fill(pin)
        await page.get_by_role("button", name="Open control desk").click()
        await page.get_by_role("link", name="Cameras", exact=True).click()
        await page.locator(".use-hls").first.check()
        await page.evaluate(
            "() => document.querySelector('camera-player').hlsInstance?.on(Hls.Events.ERROR,(_,e)=>console.log('HLS ERROR',JSON.stringify({type:e.type,details:e.details,fatal:e.fatal,reason:e.reason,error:String(e.error)})))"
        )
        await page.wait_for_timeout(15000)
        print(
            await page.locator("camera-player").first.evaluate(
                "p=>({metrics:p.metrics,error:p.video.error?.message,ready:p.video.readyState,src:p.video.currentSrc,hls:!!p.hlsInstance})"
            )
        )
        await b.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pin", required=True)
    asyncio.run(main(parser.parse_args().pin))
