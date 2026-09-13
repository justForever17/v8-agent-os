"""Actual settings/provider DOM with 1, 10 and 50 synthetic durable references."""
import asyncio
import io
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "tmp/resident-web-evidence"


async def main():
    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), (210, 40, 70)).save(buffer, format="WEBP")
    reports = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        for count in (1, 10, 50):
            context = await browser.new_context(viewport={"width": 1100, "height": 900})
            await context.add_cookies([{"name": "fixture-count", "value": str(count), "url": "http://127.0.0.1:22827"}])
            page = await context.new_page()
            errors, media_requests = [], []
            page.on("pageerror", lambda error: errors.append(error.message))

            async def route(request):
                parsed = urlparse(request.request.url)
                if parsed.netloc != "127.0.0.1:22827":
                    return await request.abort()
                if parsed.path == "/api/user-media":
                    media_requests.append(parse_qs(parsed.query).get("src", [""])[0])
                    return await request.fulfill(body=buffer.getvalue(), content_type="image/webp")
                if parsed.path == "/api/auth/session":
                    return await request.fulfill(json={"user": {"id": "fixture-owner", "email": "fixture@example.invalid"}, "expires": "2099-01-01"})
                return await request.continue_()

            await context.route("**/*", route)
            await page.goto("http://127.0.0.1:22827/background-fixture", wait_until="domcontentloaded", timeout=120000)
            await page.wait_for_function(f"document.querySelectorAll('ol[aria-label] > li').length === {count}", timeout=30000)
            await page.wait_for_function("document.documentElement.dataset.v8WallpaperKind === 'image'")
            assert await page.locator("video").count() == 0
            # Selecting a video previews metadata without starting a second decoder.
            if count > 1:
                await page.get_by_role("button", name="预览背景 2", exact=True).click()
                assert await page.locator('[data-testid="background-video-summary"]').is_visible()
                assert await page.locator("video").count() == 0
                await page.get_by_role("button", name="上移背景 2", exact=True).click()
                assert "视频" in await page.locator("ol[aria-label] > li").first.inner_text()
                await page.get_by_role("button", name="取消更改", exact=True).click()
                assert "图片" in await page.locator("ol[aria-label] > li").first.inner_text()
            full_images = {media for media in media_requests if media.endswith(".webp") and not media.endswith(".thumb.webp")}
            assert len(full_images) == 1, full_images
            assert not any(media.endswith(".mp4") for media in media_requests)
            cdp = await context.new_cdp_session(page)
            await cdp.send("Performance.enable")
            metrics = {item["name"]: item["value"] for item in (await cdp.send("Performance.getMetrics"))["metrics"]}
            reports.append({"items": count, "videos": 0, "fullImageRequests": len(full_images), "thumbnailRequests": len({m for m in media_requests if m.endswith('.thumb.webp')}), "jsHeapBytes": metrics.get("JSHeapUsedSize"), "errors": errors})
            assert not errors, errors
            await page.screenshot(path=str(OUT / f"background-settings-{count}.png"))
            await context.close()
        await browser.close()
    result = {"level": "ACTUAL_SETTINGS_SYNTHETIC_PROFILE_DEV", "resourceMatrix": reports}
    (OUT / "background-settings-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


asyncio.run(main())
