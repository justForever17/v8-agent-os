"""Cold-renderer draft tests against Chromium's real IndexedDB, with synthetic data only."""
import argparse
import asyncio
import json
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
KEY = json.dumps(["fixture-instance", "fixture-owner", "fixture-workspace", "fixture-a"], separators=(",", ":"))
SAVED_VALUES = {
    "text": "durable cold draft 中文",
    "files": [{"name": "attached.png", "type": "image/png", "size": 123, "reselect": True}],
    "sources": [{"sourceId": "source-a", "resourceRef": {"resourceId": "resource-a"}}],
    "selection": {"start": 2, "end": 8},
    "scroll": {"turnId": "older-turn", "offset": 20, "bottom": False, "top": 400},
    "skills": [{"name": "fixture-skill"}], "plugins": [{"pluginId": "fixture-plugin"}],
    "families": [{"name": "fixture-family"}], "command": {"name": "fixture-command"},
    "specMode": True, "contextSessionRefs": [{"sessionId": "fixture-ref", "source": "history_menu"}],
}


def compile_store(baseline_ref=None):
    source = subprocess.check_output(["git", "show", f"{baseline_ref}:apps/v8-agent-os-web/src/lib/composer-drafts.ts"], cwd=REPO) if baseline_ref else (APP / "src/lib/composer-drafts.ts").read_bytes()
    return subprocess.check_output([
        "node", "-e",
        "const fs=require('fs'),ts=require('typescript');process.stdout.write(ts.transpileModule(fs.readFileSync(0,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)",
    ], cwd=APP, input=source).decode("utf-8")


class FixtureServer(ThreadingHTTPServer):
    allow_reuse_address = True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-old-failure", action="store_true")
    parser.add_argument("--baseline-ref", help="Read an earlier store with git show, without changing the checkout")
    args = parser.parse_args()
    source = compile_store(args.baseline_ref)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'<!doctype html><textarea id="composer"></textarea><div id="history" style="height:100px;overflow:auto"><div style="height:1000px"></div></div>')
        def log_message(self, *_args):
            pass
    server = FixtureServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    output = REPO / "tmp/resident-web-evidence"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="draft-idb-", dir=REPO / "tmp") as profile:
        async with async_playwright() as p:
            async def launch():
                context = await p.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                page = context.pages[0]
                await page.goto(url)
                return context, page
            async def install(page, mode="normal"):
                # Delay delivery of an actual IDB get result while allowing the
                # transaction and 300 ms debounce to run. No fake database/Map.
                await page.evaluate("""mode => {
                    window.trace=[]; window.releaseRead=null;
                    const get=IDBObjectStore.prototype.get, put=IDBObjectStore.prototype.put, del=IDBObjectStore.prototype.delete;
                    window.nativeDraftGet=get; window.failReads=mode==='error';
                    IDBObjectStore.prototype.put=function(value,...args){window.trace.push({op:'put',key:value.key,fields:Object.keys(value.values),text:value.values.text});return put.call(this,value,...args)};
                    IDBObjectStore.prototype.delete=function(key){window.trace.push({op:'delete',key});return del.call(this,key)};
                    IDBObjectStore.prototype.get=function(key){
                        const request=get.call(this,key); window.trace.push({op:'get',key});
                        if(window.failReads){this.transaction.abort();return request}
                        if(mode==='error')return request;
                        if(mode==='normal')return request;
                        let callback;
                        Object.defineProperty(request,'onsuccess',{configurable:true,set(value){callback=value},get(){return callback}});
                        request.addEventListener('success',event=>{
                            window.trace.push({op:'read-complete',fields:Object.keys(request.result?.values||{})});
                            window.releaseRead=()=>callback?.call(request,event);
                        });
                        return request;
                    };
                }""", mode)
                await page.evaluate("source => { window.drafts={}; new Function('exports',source)(window.drafts); }", source)
            async def disk(page):
                return await page.evaluate("""key=>new Promise((resolve,reject)=>{
                    const open=indexedDB.open('v8-composer-drafts-v1',1);
                    open.onsuccess=()=>{const db=open.result;const tx=db.transaction('drafts');const request=window.nativeDraftGet.call(tx.objectStore('drafts'),key);request.addEventListener('success',()=>resolve(request.result||null));tx.oncomplete=()=>db.close()};open.onerror=()=>reject(open.error);
                })""", KEY)
            async def seed(page):
                await page.evaluate("""async ({key,values})=>{
                    await drafts.hydrateDraft(key);
                    for(const [field,value] of Object.entries(values))drafts.setDraftField(key,field,value,null);
                    await drafts.flushDraft(key);
                }""", {"key": KEY, "values": SAVED_VALUES})

            context, page = await launch()
            await install(page)
            await seed(page)
            assert (await disk(page))["values"] == SAVED_VALUES
            submission = await page.evaluate("async key=>{const submission=drafts.beginDraftSubmission(key);await drafts.flushDraft(key);return submission}", KEY)
            await context.close()  # Real browser shutdown; all module memory gone.

            context, page = await launch()
            await install(page, "delayed")
            await page.evaluate("key=>{window.hydration=drafts.hydrateDraft(key)}", KEY)
            await page.wait_for_function("window.releaseRead !== null")
            await page.evaluate("""key=>{
                const history=document.getElementById('history');
                history.onscroll=()=>drafts.setDraftField(key,'scroll',{turnId:'initial-turn',offset:0,bottom:true,top:900},null);
                history.scrollTop=900;
            }""", KEY)
            await page.wait_for_timeout(450)
            before_release = await page.evaluate("window.trace")
            await page.evaluate("async()=>{window.releaseRead();await window.hydration}")
            await page.evaluate("key=>drafts.flushDraft(key)", KEY)
            record = await disk(page)
            trace = await page.evaluate("window.trace")
            retained = all(record["values"].get(field) == value for field, value in SAVED_VALUES.items() if field != "scroll")
            results.append({"scenario": "cold_browser_scroll_before_hydrate", "retainedUntouchedFields": retained, "putBeforeReadDelivered": any(item["op"] == "put" for item in before_release), "trace": trace})
            if args.expect_old_failure:
                assert not retained, "old implementation unexpectedly retained the durable draft"
                (output / "drafts-indexeddb-old-failure.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(results, ensure_ascii=False))
                await context.close()
                server.shutdown()
                return
            assert retained, results[-1]
            assert not any(item["op"] == "put" for item in before_release), "must not write unknown disk fields before hydration"
            assert record["contentRevision"] == len(SAVED_VALUES) - 2
            assert record["submission"] == submission, "presentation edits cannot invalidate a submitted content revision"
            await context.close()

            context, page = await launch()
            await install(page)
            await page.evaluate("key=>drafts.hydrateDraft(key)", KEY)
            assert (await page.evaluate("key=>drafts.readDraft(key).values.text", KEY)) == SAVED_VALUES["text"]
            results.append({"scenario": "second_browser_restart", "durableTextRetained": True})
            await context.close()

            context, page = await launch()
            await install(page, "delayed")
            await page.evaluate("""key=>{
                document.getElementById('composer').oninput=event=>drafts.setDraftField(key,'text',event.target.value,'');
                drafts.setDraftField(key,'sources',previous=>[...previous,{sourceId:'new-source'}],[]);
                drafts.setDraftField(key,'selection',{start:1,end:3},null);
            }""", KEY)
            await page.locator("#composer").fill("new user typing before read")
            # No explicit hydrate: the debounce/flush must initiate the read.
            await page.wait_for_function("window.releaseRead !== null")
            await page.wait_for_timeout(450)
            assert not await page.evaluate("window.trace.some(item=>item.op==='put')")
            await page.evaluate("()=>window.releaseRead()")
            await page.evaluate("key=>drafts.flushDraft(key)", KEY)
            record = await disk(page)
            assert record["values"]["text"] == "new user typing before read"
            assert record["values"]["sources"] == [*SAVED_VALUES["sources"], {"sourceId": "new-source"}]
            assert record["values"]["files"] == SAVED_VALUES["files"]
            assert record["values"]["selection"] == {"start": 1, "end": 3}
            results.append({"scenario": "typing_and_functional_update_before_read", "newTextWins": True, "oldSourceAndAttachmentRetained": True})
            await context.close()

            context, page = await launch()
            await install(page, "error")
            before_error = await disk(page)
            await page.evaluate("key=>drafts.hydrateDraft(key)", KEY)
            failure = await page.evaluate("key=>drafts.readDraft(key)", KEY)
            assert failure["error"] is True and failure["hydrated"] is False
            await page.evaluate("""key=>{
                document.getElementById('composer').oninput=event=>drafts.setDraftField(key,'text',event.target.value,'');
            }""", KEY)
            await page.locator("#composer").fill("typing remains editable after read failure")
            await page.evaluate("key=>drafts.flushDraft(key)", KEY)
            assert (await disk(page)) == before_error, "failed reads must not permit a partial put"
            assert not await page.evaluate("window.trace.some(item=>item.op==='put')")
            await page.evaluate("async key=>{window.failReads=false;await drafts.flushDraft(key)}", KEY)
            recovered = await disk(page)
            assert recovered["values"]["text"] == "typing remains editable after read failure"
            for field, value in before_error["values"].items():
                if field != "text":
                    assert recovered["values"][field] == value, field
            assert await page.evaluate("key=>drafts.readDraft(key).hydrated&&!drafts.readDraft(key).error", KEY)
            results.append({"scenario": "real_read_transaction_abort_then_retry", "diskUntouchedOnFailure": True, "retryMergesEditableText": True})
            await context.close()

            context, page = await launch()
            await install(page, "delayed")
            await page.evaluate("key=>{window.hydration=drafts.hydrateDraft(key)}", KEY)
            await page.wait_for_function("window.releaseRead !== null")
            await page.evaluate("""key=>{
                drafts.setDraftField(key,'scroll',{bottom:true,top:1},null);
                window.flushing=drafts.flushDraft(key);
                window.removing=drafts.removeDrafts(candidate=>candidate===key);
            }""", KEY)
            await page.evaluate("async()=>{window.releaseRead();await Promise.all([window.hydration,window.flushing,window.removing])}")
            assert await disk(page) is None
            assert not await page.evaluate("key=>drafts.readDraft(key).values.text", KEY)
            await context.close()
            context, page = await launch()
            await install(page)
            await page.evaluate("key=>drafts.hydrateDraft(key)", KEY)
            assert not await page.evaluate("key=>drafts.readDraft(key).values.text", KEY)
            assert await disk(page) is None
            results.append({"scenario": "logout_during_pending_read_and_flush_then_restart", "deletedDraftNotResurrected": True})
            await context.close()
    server.shutdown()
    (output / "drafts-indexeddb-lifecycle.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


asyncio.run(main())
