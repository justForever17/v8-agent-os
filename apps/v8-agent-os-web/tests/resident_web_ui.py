"""Actual product React tree, synthetic HTTP/SSE; run prepare_resident_ui_fixture.py first."""
import asyncio
import base64
import json
import statistics
from pathlib import Path
from time import perf_counter
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "tmp" / "resident-web-evidence"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2jDMAAAAASUVORK5CYII=")


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append({"message":error.message,"stack":error.stack}))
        cdp = await context.new_cdp_session(page)
        await cdp.send("Runtime.enable")
        cdp.on("Runtime.exceptionThrown", lambda payload: errors.append(payload["exceptionDetails"]))
        state = {"fail_detail": False, "submit_ok": False, "submits": [], "requests": []}
        sessions = [{"id": f"fixture-{letter}", "sessionId": f"fixture-{letter}", "title": f"测试会话 {letter.upper()}", "workspaceId": f"workspace-{letter}", "workspacePath": f"E:/synthetic/{letter}", "createdAt": "2026-09-13T00:00:00Z"} for letter in ["a", "b"]]
        queue = {"id": "fixture-queue", "sessionId": "fixture-a", "content": "合成等待消息", "state": "pending", "ordinal": 1}
        await context.add_init_script('''
          window.__urls={created:0,revoked:0};
          const create=URL.createObjectURL.bind(URL), revoke=URL.revokeObjectURL.bind(URL);
          URL.createObjectURL=b=>{window.__urls.created++;return create(b)};
          URL.revokeObjectURL=u=>{window.__urls.revoked++;revoke(u)};
          window.__streams=[];
          class FixtureStream extends EventTarget {
            constructor(url){super();this.url=url;this.readyState=1;window.__streams.push(this)}
            close(){this.readyState=2}
            send(type,data){this.dispatchEvent(new MessageEvent(type,{data:JSON.stringify(data)}))}
          }
          window.EventSource=FixtureStream;
        ''')

        async def route(request_route):
            from urllib.parse import urlparse
            request = request_route.request
            url = urlparse(request.url)
            if url.netloc != "127.0.0.1:22827":
                return await request_route.abort()
            path = url.path
            if path.startswith("/api/"): state["requests"].append(path)
            if not path.startswith("/api/"):
                return await request_route.continue_()
            body = {}
            if path == "/api/auth/session":
                body = {"user": {"id": "fixture-owner", "email": "fixture@example.invalid", "name": "测试用户"}, "expires": "2099-01-01T00:00:00Z"}
            elif path == "/api/client/instance": body = {"instanceId": "fixture-instance"}
            elif path == "/api/conversations": body = sessions
            elif path.endswith("/detail"):
                if state["fail_detail"]: return await request_route.fulfill(status=503, json={"error": "fixture unavailable"})
                sid = path.split("/")[3]
                body = {"id": sid, "sessionId": sid, "projection": {"sessionId": sid, "queuedMessages": [queue] if sid == "fixture-a" else [], "queuedMessagesWindow": {"complete": True, "hasMore": False}, "runtimeStatus": "idle", "messages": [], "latestSeq": 1}, "processes": []}
            elif path.endswith("/turns"): body = {"messages": [], "pageInfo": {"totalTurnCount": 0, "hasMore": False}}
            elif path.endswith("/turn-index"): body = {"entries": [], "totalTurnCount": 0}
            elif path.endswith("/scope"):
                letter = path.split("/")[3][-1]
                body = {"binding": {"sessionId": f"fixture-{letter}", "workspaceId": f"workspace-{letter}", "workspacePath": f"E:/synthetic/{letter}", "status": "active", "resolvedScope": "workspace"}}
            elif path == "/api/supervisor-profile": body = {"name": "测试主管", "roleLabel": "主管"}
            elif path == "/api/projects": body = {"projects": [], "mainWorkspacePath": "E:/synthetic/main"}
            elif path == "/api/runs": body = {"runs": []}
            elif path.endswith("/processes"): body = {"processes": []}
            elif path == "/api/upload": body = {"id": "source-fixture", "sourceId": "source-fixture", "url": "/api/fixture.png", "previewUrl": "/api/fixture.png", "type": "image/png", "name": "fixture.png", "resourceRef": {"resourceId": "source-fixture"}}
            elif path == "/api/fixture.png": return await request_route.fulfill(content_type="image/png", body=PNG)
            elif path in ["/api/chat-submit", "/api/chat"]:
                state["submits"].append(request.post_data_json)
                await asyncio.sleep(2)
                if not state["submit_ok"]: return await request_route.fulfill(status=503, json={"error": "fixture rejection"})
                body = {"accepted": True, "queued": True, "queuedMessage": queue, "runId": "fixture-run", "session_id": "fixture-a"}
            elif "supervisor-reasoning-effort" in path: body = {"visible": False, "levels": ["auto"]}
            return await request_route.fulfill(json=body)

        await context.route("**/*", route)
        result = {"level": "actual React components with synthetic transport", "errors": errors}
        try:
            await page.goto("http://127.0.0.1:22827/chat?id=fixture-a", wait_until="domcontentloaded", timeout=120000)
            composer = page.locator('textarea[data-v8os-chat-composer="true"]')
            await expect(composer).to_be_enabled(timeout=30000)
            await composer.fill("会话 A 草稿")
            await composer.evaluate("e=>{e.setSelectionRange(2,4);e.dispatchEvent(new Event('select',{bubbles:true}))}")
            await page.locator('form input[type="file"]').last.set_input_files({"name": "fixture.png", "mimeType": "image/png", "buffer": PNG})
            await page.wait_for_timeout(400)
            urls_before = await page.evaluate("({...window.__urls})")
            await composer.press_sequentially("abcdefghijklmnopqrstuvwxyz", delay=10)
            urls_after = await page.evaluate("({...window.__urls})")
            assert urls_after["created"] == urls_before["created"], (urls_before, urls_after)
            a = await composer.input_value()
            await page.get_by_text("b", exact=True).click()
            await page.get_by_text("测试会话 B", exact=True).click()
            await expect(composer).to_be_enabled()
            await expect(composer).to_have_value("")
            await composer.fill("会话 B 草稿")
            await page.get_by_text("测试会话 A", exact=True).click()
            await expect(composer).to_be_enabled()
            await expect(composer).to_have_value(a)
            assert await page.locator('img[alt="preview"]').count() == 1
            await page.wait_for_timeout(400)
            await page.reload(wait_until="domcontentloaded")
            await expect(composer).to_be_enabled()
            await expect(composer).to_have_value(a)
            assert await page.locator('img[alt="preview"]').count() == 1
            state["fail_detail"] = True
            await page.evaluate("()=>window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/')).forEach(s=>s.dispatchEvent(new Event('error')))")
            await page.wait_for_timeout(400)
            await expect(page.get_by_text("合成等待消息", exact=True)).to_be_visible()
            await page.evaluate("q=>window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/')).forEach(s=>s.send('snapshot',{sessionId:'fixture-a',latestSeq:20,queuedMessages:[q],queuedMessagesWindow:{complete:true},runtimeStatus:'idle'}))", queue)
            await page.evaluate("()=>window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/')).forEach(s=>s.send('snapshot',{sessionId:'fixture-a',latestSeq:1,queuedMessages:[],queuedMessagesWindow:{complete:true},runtimeStatus:'idle'}))")
            await expect(page.get_by_text("合成等待消息", exact=True)).to_be_visible()
            state["fail_detail"] = False
            await composer.fill("提交版本一")
            await composer.press("Enter")
            await page.wait_for_timeout(150)
            await composer.fill("提交期间版本二")
            await page.wait_for_timeout(2300)
            await expect(composer).to_have_value("提交期间版本二")
            assert state["submits"], "submission must reach the real transport boundary"
            samples=[]
            if not await page.get_by_text("测试会话 B", exact=True).is_visible():
                await page.get_by_text("b", exact=True).click()
            for _ in range(30):
                await page.get_by_text("测试会话 B", exact=True).click()
                await expect(composer).to_have_value("会话 B 草稿")
                start=perf_counter()
                await page.get_by_text("测试会话 A", exact=True).click()
                await expect(composer).to_have_value("提交期间版本二")
                samples.append((perf_counter()-start)*1000)
            result.update({"drafts": "A/B, reload, attachment and delayed failure preserved", "queue": "503 and stale empty snapshot preserved", "objectUrls": {"before":urls_before,"afterTyping":urls_after,"final":await page.evaluate("({...window.__urls})")}, "switchMs": {"n":len(samples),"median":statistics.median(samples),"p95":sorted(samples)[28]}, "activeSessionStreams":await page.evaluate("window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/')).length")})
            await page.screenshot(path=str(OUTPUT / "web-drafts.png"))
            assert not errors, errors
        finally:
            result["requests"] = state["requests"]
            result["draftState"] = await page.locator("form[data-draft-ready]").evaluate_all("items=>items.map(e=>({...e.dataset}))")
            (OUTPUT / "web-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            await page.screenshot(path=str(OUTPUT / "web-last.png"))
            await browser.close()
        print(json.dumps(result,ensure_ascii=False))


asyncio.run(main())
