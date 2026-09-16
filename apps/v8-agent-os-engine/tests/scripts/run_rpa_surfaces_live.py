"""Isolated production Admin/Web interaction with disposable local file effects."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--admin", default="http://127.0.0.1:19628")
    parser.add_argument("--web", default="http://127.0.0.1:19627")
    args = parser.parse_args()
    output, state = Path(args.output).resolve(), Path(args.state).resolve()
    if not args.live or output.exists() or state == (Path.home() / ".v8-agent-os").resolve():
        parser.error("Requires --live, isolated state, fresh output")
    if any(not url.startswith("http://127.0.0.1:") for url in (args.admin, args.web)):
        parser.error("Owned loopback fixtures only")
    output.mkdir(parents=True)
    os.environ["V8_AGENT_OS_HOME"] = str(state)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.client_identity import get_identity_service
    from playwright.sync_api import sync_playwright
    service = get_identity_service()
    owner, password = service.owners.owner(), secrets.token_urlsafe(24)
    service.owners.change_password(password, force=True, now=service.clock())
    source, target = output / "input.txt", output / "copied.txt"
    source.write_text("owned production UI fixture", encoding="utf-8")
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(args.admin + "/login", wait_until="networkidle")
        page.locator("#login").fill(owner["login"])
        page.locator("#password").fill(password)
        page.get_by_role("button", name="登录", exact=True).click()
        page.wait_for_url("**/admin", timeout=30000)
        page.goto(args.admin + "/admin/rpa", wait_until="networkidle")
        page.get_by_role("button", name="新建流程", exact=True).click()
        name = "Owned UI fixture " + output.name
        page.locator("#rpa-flow-name").fill(name)
        page.locator("#rpa-flow-goal").fill(output.name + ": Copy one disposable local fixture and inspect the result")
        page.get_by_role("button", name="复制文件", exact=True).click()
        page.get_by_text("完整步骤 JSON（高级编辑）", exact=True).click()
        editor = page.get_by_role("textbox", name="完整步骤 JSON（高级编辑）")
        step = json.loads(editor.input_value())
        step["params"] = {"source": source.as_posix(), "target": target.as_posix()}
        editor.fill(json.dumps(step))
        page.wait_for_function("sessionStorage.getItem('v8.rpa.studio.unsaved')?.includes('copied.txt')")
        page.reload(wait_until="networkidle")
        assert page.locator("#rpa-flow-name").input_value() == name
        checks.append("dirty editor survives reload")
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/rpa/drafts")) as response:
            page.get_by_role("button", name="保存新草稿", exact=True).click()
        saved = response.value.json()
        assert response.value.ok, saved
        draft = saved.get("draft", saved)
        checks.append("UI created draft")
        page.get_by_role("button", name="运行", exact=True).click()
        with page.expect_response(lambda r: "/api/rpa/drafts/" in r.url and r.url.endswith("/run"), timeout=120000) as response:
            page.get_by_role("button", name="保存并试跑当前草稿", exact=True).click()
        run = response.value.json()
        assert response.value.ok and run.get("status") == "completed", run
        assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
        checks.append("Admin save and run produced exact file content")
        page.screenshot(path=str(output / "admin-run.png"), full_page=True)
        with page.expect_response(lambda r: "/api/rpa/" in r.url and "approve" in r.url and r.request.method == "POST", timeout=30000) as response:
            page.get_by_role("button", name="批准草稿为模板", exact=True).click()
        approval = response.value.json()
        assert response.value.ok, approval
        page.goto(args.web + "/rpa", wait_until="networkidle")
        option = page.locator("select option").filter(has_text=output.name).first
        option.wait_for(state="attached")
        page.locator("select").first.select_option(value=option.get_attribute("value"))
        source.write_text("new source verifies the Web execution", encoding="utf-8")
        with page.expect_response(lambda r: "/api/rpa/templates/" in r.url and r.url.endswith("/run"), timeout=120000) as response:
            page.get_by_role("button", name="启动自动流程", exact=True).click()
        web_run = response.value.json()
        assert response.value.ok and web_run.get("status") == "completed", web_run
        assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
        page.reload(wait_until="networkidle")
        page.get_by_text("最近运行", exact=True).wait_for(timeout=15000)
        checks.append("Web template run produced the new file content; history survives reload")
        page.screenshot(path=str(output / "web-template.png"), full_page=True)
        # Set up a second owned template through the real Admin API, then use
        # Web controls while the real Robot process is waiting before its write.
        late_target = output / "must-not-exist.txt"
        page.goto(args.admin + "/admin/rpa", wait_until="networkidle")
        fixture = {"name": output.name + " cancel", "goal": output.name + " cancel before copy", "appId": "desktop",
                   "steps": [{"stepId": "wait", "use": "wait", "params": {"seconds": 60}},
                             {"stepId": "copy", "use": "file_copy", "params": {"source": source.as_posix(), "target": late_target.as_posix()}}]}
        created = page.evaluate("async body => { const r=await fetch('/api/rpa/drafts',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}); return {ok:r.ok,body:await r.json()}; }", fixture)
        assert created["ok"], created
        cancel_draft = created["body"].get("draft", created["body"])
        approved = page.evaluate("async id => { const r=await fetch('/api/rpa/drafts/'+encodeURIComponent(id)+'/approve-template',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}); return {ok:r.ok,body:await r.json()}; }", cancel_draft["id"])
        assert approved["ok"], approved
        page.goto(args.web + "/rpa", wait_until="networkidle")
        option = page.locator("select option").filter(has_text=output.name + " cancel").first
        option.wait_for(state="attached")
        page.locator("select").first.select_option(value=option.get_attribute("value"))
        with page.expect_response(lambda r: "/api/rpa/templates/" in r.url and r.url.endswith("/run"), timeout=120000) as completion:
            with page.expect_request(lambda r: "/api/rpa/templates/" in r.url and r.url.endswith("/run")) as request:
                page.get_by_role("button", name="启动自动流程", exact=True).click()
            cancel_run_id = request.value.post_data_json["runId"]
            # wait_for_function treats a Promise as truthy before its boolean
            # result settles. Explicitly await each HTTP read before polling.
            deadline = time.monotonic() + 60
            while not page.evaluate("async id => {const r=await fetch('/api/runs?limit=40');const p=await r.json();return (p.runs||[]).some(x=>(x.id||x.run_id)===id&&x.metadata?.executionState==='running_robot')}", cancel_run_id):
                if time.monotonic() >= deadline:
                    raise AssertionError("Engine did not confirm the selected run as running_robot")
                page.wait_for_timeout(200)
            ui_receipt = page.locator('[role="status"]').all_text_contents()
            assert any(cancel_run_id in receipt for receipt in ui_receipt), ui_receipt
            with page.expect_response(lambda r: r.url.endswith("/commands/cancel")) as stop_response:
                page.get_by_role("button", name="停止流程", exact=True).click()
            stop_result = stop_response.value.json()
            (output / "stop-response.json").write_text(json.dumps({
                "requestedRunId": cancel_run_id, "uiReceipt": ui_receipt,
                "url": stop_response.value.url, "status": stop_response.value.status,
                "body": stop_result,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            assert stop_response.value.ok, stop_result
        cancelled = completion.value.json()
        assert cancelled.get("status") == "cancelled", cancelled
        assert not late_target.exists()
        page.reload(wait_until="networkidle")
        page.get_by_text("最近运行", exact=True).click()
        page.get_by_text("已取消", exact=True).first.wait_for(timeout=15000)
        checks.append("Web stop cancelled the real Robot process before its file write; cancelled history survives reload")
        page.screenshot(path=str(output / "web-cancelled.png"), full_page=True)
        browser.close()
    report = {"ok": True, "checks": checks, "draftId": draft.get("id"), "runId": run.get("runId"), "webRunId": web_run.get("runId")}
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
