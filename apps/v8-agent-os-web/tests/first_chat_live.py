"""Full production Web BFF / isolated Engine / governed provider first-chat smoke."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:22927")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--expect-failure", action="store_true")
    parser.add_argument("--expected-failure-text", default="503")
    parser.add_argument("--inspect-failed-session", help="Reopen an already persisted synthetic provider failure without submitting again")
    args = parser.parse_args()
    if not args.live: parser.error("--live required")
    state = args.state.resolve()
    if state == (Path.home()/".v8-agent-os").resolve(): parser.error("isolated state required")
    workspace = state/"workspace"/"browser-first-chat"
    workspace.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    result = {"layer": "production Web BFF + isolated real Engine + configured provider", "requests": [], "errors": []}
    start = perf_counter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width":1440,"height":900})
        await context.add_init_script('''
          window.__events=[];window.__rows=[];window.__rowIds=new WeakMap();window.__nextRow=0;
          window.__documentId=crypto.randomUUID();window.__streams=[];
          const Native=window.EventSource;
          window.EventSource=class extends Native {
            constructor(url,opts){super(url,opts);window.__streams.push(this);
              for(const kind of ['snapshot','runtime','error'])this.addEventListener(kind,e=>{
                let d={};try{d=JSON.parse(e.data)}catch{};
                const p=d.payload||d;window.__events.push({at:performance.now(),url,kind,seq:d.seq||d.latestSeq||0,
                  topic:d.topic||'',runId:d.run_id||d.runId||'',contextEpoch:d.contextEpoch,
                  messageCount:(p.messages||p.snapshot?.messages||[]).length,
                  chars:String(p.content||p.delta||p.text||'').length});
              });
            }
          };
          addEventListener('DOMContentLoaded',()=>new MutationObserver(()=>{
            const rows=[...document.querySelectorAll('.min-h-full > div:has(> .mx-auto)')].map(e=>{
              if(!window.__rowIds.has(e))window.__rowIds.set(e,++window.__nextRow);
              return {id:window.__rowIds.get(e),chars:e.textContent.length,y:e.getBoundingClientRect().y};
            });
            const v=JSON.stringify(rows);if(v!==window.__lastRows){window.__rows.push({at:performance.now(),rows});window.__lastRows=v;}
          }).observe(document.body,{childList:true,subtree:true,characterData:true}));
        ''')
        page = await context.new_page()
        page.on("pageerror", lambda e: result["errors"].append(str(e)))
        page.on("response", lambda r: result["requests"].append({"path": urlparse(r.url).path, "status": r.status, "at": round((perf_counter()-start)*1000)}) if "/api/" in r.url else None)
        page.on("dialog", lambda d: d.accept())
        try:
            if args.inspect_failed_session:
                assert args.expect_failure
                proof = json.loads((state/"provider-fault-proof.json").read_text())
                await page.goto(args.url+"/chat?id="+args.inspect_failed_session, wait_until="domcontentloaded")
                alert = page.locator('[role="alert"]:not(#__next-route-announcer__)')
                composer = page.locator('textarea[data-v8os-chat-composer="true"]')
                await expect(alert).to_contain_text(args.expected_failure_text, timeout=30000)
                result["failureBeforeReload"] = await alert.inner_text()
                await composer.fill("")
                await page.get_by_role("button", name="重新编辑", exact=True).click()
                await expect(composer).to_have_value("你好")
                result["restoredFailedInput"] = True
                await page.wait_for_timeout(400)
                await page.reload(wait_until="domcontentloaded")
                await expect(alert).to_contain_text(args.expected_failure_text, timeout=30000)
                await expect(composer).to_have_value("你好")
                result["failureAfterReload"] = await alert.inner_text()
                assert result["failureBeforeReload"] == result["failureAfterReload"]
                after = json.loads((state/"provider-fault-proof.json").read_text())
                assert after == proof
                result["providerFault"] = proof
                result["reloadWithoutReplay"] = True
                await page.screenshot(path=str(args.out/"persisted-failure.png"))
                print("Persisted Engine 503: reason, edit recovery, draft reload and no request replay passed")
                return
            await page.goto(args.url+"/chat?new=1", wait_until="domcontentloaded", timeout=120000)
            create = page.get_by_role("button", name="创建并开始", exact=True)
            await expect(create).to_be_visible(timeout=60000)
            await page.locator("input:visible").last.fill(str(workspace))
            await create.click()
            composer = page.locator('textarea[data-v8os-chat-composer="true"]')
            await expect(composer).to_be_enabled(timeout=30000)
            result["workspaceReadyMs"] = round((perf_counter()-start)*1000)
            result["documentBefore"] = await page.evaluate("window.__documentId")
            await composer.fill("你好")
            before = perf_counter()
            async with page.expect_response(lambda r: urlparse(r.url).path == "/api/chat-submit", timeout=60000) as accepted:
                await composer.press("Enter")
            response = await accepted.value
            ack = await response.json()
            result["accepted"] = {k:ack.get(k) for k in ["accepted","runId","conversationId"]}
            result["acceptedMs"] = round((perf_counter()-before)*1000)
            await page.screenshot(path=str(args.out/"accepted.png"))
            if not args.expect_failure:
                await page.wait_for_function("window.__events.some(e=>e.topic==='run.text.delta'&&e.chars>0)", timeout=120000)
                result["firstTextMs"] = round((perf_counter()-before)*1000)
            await page.wait_for_function("window.__events.some(e=>['run.completed','run.failed','run.cancelled'].includes(e.topic))", timeout=120000)
            result["terminalMs"] = round((perf_counter()-before)*1000)
            if args.expect_failure:
                await page.wait_for_function("window.__events.some(e=>e.topic==='run.failed')")
                alert = page.locator('[role="alert"]:not(#__next-route-announcer__)')
                await expect(alert).to_contain_text(args.expected_failure_text, timeout=15000)
                result["failureBeforeReload"] = await alert.inner_text()
                proof = json.loads((state/"provider-fault-proof.json").read_text())
                assert proof["requests"] > 0 and proof["status"] == 503, proof
                result["providerFault"] = proof
                await page.get_by_role("button", name="重新编辑", exact=True).click()
                await expect(composer).to_have_value("你好")
                result["restoredFailedInput"] = True
            else:
                await expect(page.locator('.min-h-full > div:has(> .mx-auto)')).to_have_count(2)
            await page.screenshot(path=str(args.out/"complete.png"))
            result["documentAfter"] = await page.evaluate("window.__documentId")
            result["rowsBeforeReload"] = await page.locator('.min-h-full > div:has(> .mx-auto)').evaluate_all("es=>es.map(e=>e.textContent)")
            await composer.fill("未发送的恢复草稿")
            await page.wait_for_timeout(400)  # documented draft persistence debounce
            result["events"] = await page.evaluate("window.__events")
            result["rowChanges"] = await page.evaluate("window.__rows")
            await page.reload(wait_until="domcontentloaded")
            await expect(composer).to_have_value("未发送的恢复草稿", timeout=30000)
            if args.expect_failure:
                await expect(alert).to_contain_text(args.expected_failure_text, timeout=15000)
                result["failureAfterReload"] = await alert.inner_text()
                assert result["failureAfterReload"] == result["failureBeforeReload"]
                assert json.loads((state/"provider-fault-proof.json").read_text())["requests"] == result["providerFault"]["requests"], "reload/edit must not replay the failed run"
            else:
                await expect(page.locator('.min-h-full > div:has(> .mx-auto)')).to_have_count(2)
            result["rowsAfterReload"] = await page.locator('.min-h-full > div:has(> .mx-auto)').evaluate_all("es=>es.map(e=>e.textContent)")
            result["draftRecovered"] = True
            result["activeSessionStreams"] = await page.evaluate("window.__streams.filter(s=>s.readyState!==2&&s.url.includes('/sessions/')).length")
            await page.screenshot(path=str(args.out/"reload.png"))
        finally:
            result.setdefault("events", await page.evaluate("window.__events||[]"))
            result.setdefault("rowChanges", await page.evaluate("window.__rows||[]"))
            for key in ["rowsBeforeReload","rowsAfterReload"]:
                if key in result: result[key] = [{"chars":len(v),"hash":hashlib.sha256(v.encode()).hexdigest()} for v in result[key]]
            result["finalUrl"] = page.url
            (args.out/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            await page.screenshot(path=str(args.out/"last.png"))
            await browser.close()
    print(json.dumps({k:v for k,v in result.items() if k not in {"requests","events","rowChanges"}},ensure_ascii=False))


if __name__ == "__main__": asyncio.run(main())
