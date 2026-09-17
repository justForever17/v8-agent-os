"""Production React first-chat race audit with synthetic HTTP/SSE boundaries.

Run prepare_resident_ui_fixture.py and serve its production build first.
This is not a provider, installer, or Windows 10 acceptance test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:22827")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenario", choices=["normal", "snapshot-before-event", "recorded-burst", "failed", "delayed-instance"], default="normal")
    parser.add_argument("--no-screenshots", action="store_true")
    parser.add_argument("--assert-fixed", action="store_true")
    parser.add_argument("--reduced-motion", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    result = {"layer": "production React, synthetic HTTP/SSE", "requests": [], "console": [], "errors": []}
    state = {"sessions": [], "messages": [], "submits": [], "run": None}
    instance_gate = asyncio.Event()
    sid = "first-chat-fixture"
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce" if args.reduced_motion else "no-preference")
        await context.add_init_script('''
            window.__streams=[]; window.__domChanges=[]; window.__rowIds=new WeakMap(); window.__nextRow=0;
            window.__rows=()=>[...document.querySelectorAll('.min-h-full > div:has(> .mx-auto)')].map(e=>{
              if(!window.__rowIds.has(e))window.__rowIds.set(e,++window.__nextRow);
              return {id:window.__rowIds.get(e),text:e.textContent.slice(0,160),y:e.getBoundingClientRect().y};
            });
            class FixtureStream extends EventTarget {
              constructor(url){super();this.url=url;this.readyState=1;window.__streams.push(this)}
              close(){this.readyState=2}
              send(type,data){this.dispatchEvent(new MessageEvent(type,{data:JSON.stringify(data)}))}
            }
            window.EventSource=FixtureStream;
            addEventListener('DOMContentLoaded',()=>new MutationObserver(()=>{
              const rows=window.__rows();
              const value=JSON.stringify(rows);if(value!==window.__lastRows){window.__domChanges.push({at:performance.now(),rows});window.__lastRows=value;}
            }).observe(document.body,{childList:true,subtree:true,characterData:true}));
        ''')
        page = await context.new_page()
        async def screenshot(name):
            if not args.no_screenshots: await page.screenshot(path=str(args.out/name))
        page.on("pageerror", lambda error: result["errors"].append(str(error)))
        page.on("console", lambda message: result["console"].append(message.text) if message.type in {"error", "warning"} or "ChatClient" in message.text else None)

        def projection():
            return {"sessionId": sid, "messages": state["messages"], "latestSeq": 0,
                    "runtimeStatus": state["run"]["status"] if state["run"] else "idle",
                    "currentRun": state["run"], "queuedMessages": [], "queuedMessagesWindow": {"complete": True}}

        async def route(r):
            request = r.request
            path = urlparse(request.url).path
            if urlparse(request.url).netloc != urlparse(args.url).netloc:
                return await r.abort()
            if not path.startswith("/api/"):
                return await r.continue_()
            result["requests"].append({"path": path, "method": request.method, "at": round((perf_counter()-started)*1000)})
            body = {}
            if path == "/api/auth/session":
                body = {"user": {"id": "fixture-owner", "email": "fixture@example.invalid", "name": "测试用户"}, "expires": "2099-01-01T00:00:00Z"}
            elif path == "/api/client/instance":
                if args.scenario == "delayed-instance": await instance_gate.wait()
                body = {"instanceId": "fixture-instance"}
            elif path == "/api/conversations":
                if request.method == "POST":
                    body = {"id": sid, "sessionId": sid, "title": "首聊测试", "workspaceId": "fixture-workspace", "workspacePath": "E:/synthetic/first-chat", "createdAt": "2026-09-17T00:00:00Z"}
                    state["sessions"].append(body)
                else: body = state["sessions"]
            elif path == "/api/projects":
                project = {"id": "fixture-project", "workspaceId": "fixture-workspace", "workspacePath": "E:/synthetic/first-chat", "name": "首聊工作区", "defaultScope": "workspace"}
                body = project if request.method == "POST" else {"projects": [], "mainWorkspacePath": "E:/synthetic/main"}
            elif path.endswith("/scope"):
                body = {"binding": {"sessionId": sid, "workspaceId": "fixture-workspace", "workspacePath": "E:/synthetic/first-chat", "status": "active", "resolvedScope": "workspace"}}
            elif path.endswith("/detail"): body = {"id": sid, "projection": projection(), "processes": []}
            elif path.endswith("/turns"): body = {"messages": state["messages"], "pageInfo": {"totalTurnCount": int(bool(state["messages"])), "hasMore": False}}
            elif path.endswith("/turn-index"): body = {"turns": [], "pageInfo": {"totalTurnCount": int(bool(state["messages"]))}}
            elif path == "/api/supervisor-profile": body = {"name": "测试主管", "roleLabel": "主管"}
            elif path == "/api/runs": body = {"runs": [state["run"]] if state["run"] else []}
            elif path.endswith("/processes"): body = {"processes": []}
            elif "supervisor-reasoning-effort" in path: body = {"visible": False, "levels": ["auto"]}
            elif path == "/api/chat-submit":
                payload = request.post_data_json
                state["submits"].append(payload)
                state["run"] = {"id": "fixture-run", "status": "running", "sessionId": sid}
                state["messages"] = [{"id": payload["clientMessageId"], "role": "user", "content": "你好", "runId": "fixture-run", "turnId": "fixture-turn", "turnPosition": 1, "timestamp": 1}]
                body = {"accepted": True, "queued": False, "runId": "fixture-run", "conversationId": sid}
            return await r.fulfill(json=body)

        await context.route("**/*", route)
        async def send(kind, payload):
            await page.evaluate("([sid,kind,payload])=>window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/'+sid+'/')).forEach(s=>s.send(kind,payload))", [sid, kind, payload])
        try:
            await page.goto(args.url+"/chat?new=1", wait_until="domcontentloaded" if args.scenario == "delayed-instance" else "networkidle", timeout=120000)
            await screenshot("01-workspace.png")
            result["initialText"] = await page.locator("body").inner_text()
            path_input = page.locator("input:visible").last
            await path_input.fill("E:/synthetic/first-chat")
            await page.get_by_role("button", name="创建并开始", exact=True).click()
            composer = page.locator('textarea[data-v8os-chat-composer="true"]')
            if args.scenario == "delayed-instance":
                await page.wait_for_url("**/chat?id="+sid)
                await expect(composer).to_have_count(0)
                assert state["submits"] == [], "unresolved instance must not accept a submission or a draft under a guessed owner"
                result["instanceBarrier"] = "workspace/session created; no editable composer or submit before manifest; first send after manifest"
                instance_gate.set()
            await expect(composer).to_be_enabled(timeout=20000)
            await composer.fill("你好")
            result["readyMs"] = round((perf_counter()-started)*1000)
            async with page.expect_response(lambda response: urlparse(response.url).path == "/api/chat-submit"):
                await composer.press("Enter")
            await expect(page.locator('.min-h-full > div:has(> .mx-auto)')).to_have_count(2)
            await screenshot("02-placeholder.png")
            result["placeholderIds"] = await page.evaluate("window.__rows().map(r=>r.id)")
            if args.assert_fixed:
                waiting = page.locator('[data-assistant-state="waiting"]')
                await expect(waiting.get_by_role("status").locator("span")).to_have_count(3)
                assert await waiting.locator("img").count() == 0
                styles = await waiting.locator(":scope > div").evaluate("e=>({background:getComputedStyle(e).backgroundColor,shadow:getComputedStyle(e).boxShadow,border:getComputedStyle(e).borderWidth})")
                assert styles == {"background":"rgba(0, 0, 0, 0)","shadow":"none","border":"0px"}, styles
                if args.reduced_motion:
                    assert await waiting.get_by_role("status").locator("span").evaluate_all("es=>es.every(e=>getComputedStyle(e).animationName==='none')")
            if args.scenario == "snapshot-before-event":
                state["messages"].append({"id": "canonical-assistant", "role": "assistant", "runId": "fixture-run",
                                         "content": "", "timestamp": 2, "metadata": {"transcriptVersion": 1},
                                         "nodes": [{"id": "agent-start", "kind": "execution", "executionType": "agent_start", "agentName": "测试主管"}]})
            await send("snapshot", projection())
            events = [{"seq": 1, "topic": "agent.started", "run_id": "fixture-run", "payload": {"agentName": "测试主管"}},
                      {"seq": 2, "topic": "run.text.delta", "run_id": "fixture-run", "message_id": "canonical-assistant", "payload": {"content": "你好！有什么我可以帮你？"}}]
            if args.scenario == "recorded-burst":
                events.insert(0, {"seq": 1, "topic": "message.user.recorded", "run_id": "fixture-run", "payload": {"message_id": state["submits"][0]["clientMessageId"], "content": "你好"}})
                for i, event in enumerate(events): event["seq"] = i+1
            if args.scenario == "failed":
                state["run"]["status"] = "failed"
                state["run"]["error"] = "Provider temporarily unavailable (503)"
                events = [{"seq": 3, "topic": "run.failed", "run_id": "fixture-run", "payload": {"error": state["run"]["error"], "code": "provider_unavailable"}}]
            await page.evaluate("([sid,events])=>{window.__sentAt=performance.now();window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/'+sid+'/')).forEach(s=>events.forEach(e=>s.send('runtime',e)))}", [sid, events])
            if args.scenario != "failed":
                await page.wait_for_function("window.__rows().some(r=>r.text.includes('你好！'))")
                result["eventToDomMs"] = await page.evaluate("window.__domChanges.find(c=>c.at>=window.__sentAt&&c.rows.some(r=>r.text.includes('你好！'))).at-window.__sentAt")
            else:
                await expect(composer).to_be_enabled()
                if args.assert_fixed:
                    alert = page.get_by_role("alert").filter(has_text="Provider temporarily unavailable (503)")
                    await expect(alert).to_be_visible()
                    await send("snapshot", {**projection(), "latestSeq": 1, "runtimeStatus": "running", "currentRun": {"id": "fixture-run", "status": "running"}, "messages": []})
                    await expect(alert).to_be_visible()
                    await page.get_by_role("button", name="重新编辑", exact=True).click()
                    await expect(composer).to_have_value("你好")
                    await page.wait_for_timeout(400)
                    await page.reload(wait_until="domcontentloaded")
                    await expect(alert).to_be_visible()
                    await expect(composer).to_have_value("你好")
            await page.evaluate("()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
            result["afterTextEvent"] = await page.evaluate("window.__rows()")
            if args.assert_fixed and args.scenario != "failed":
                assert [r["id"] for r in result["afterTextEvent"]] == result["placeholderIds"], "first event must keep both DOM identities"
                assert max(len(c["rows"]) for c in await page.evaluate("window.__domChanges")) == 2, "no third empty assistant"
            await screenshot("03-stream.png")
            result["streamCounts"] = await page.evaluate("({created:window.__streams.length,active:window.__streams.filter(s=>s.readyState===1&&s.url.includes('/sessions/')).length})")
        finally:
            result["domChanges"] = await page.evaluate("window.__domChanges")
            result["finalText"] = await page.locator("body").inner_text()
            result["requestCounts"] = dict(Counter(x["path"] for x in result["requests"]))
            result["submits"] = state["submits"]
            (args.out/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            await screenshot("last.png")
            await browser.close()
        print(json.dumps({key: result.get(key) for key in ["readyMs", "placeholderIds", "afterTextEvent", "streamCounts", "requestCounts", "errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
