"""Real React Spec review UI with synthetic HTTP; no Next/Admin/Engine/provider."""
import argparse
import functools
import hashlib
import json
import os
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

APP = Path(__file__).resolve().parents[1]
OUTPUT = APP.parents[1] / "tmp/spec-review-ui"
EDITED = "\ufeff# User edited version\n\n" + "中文完整段落。\n" * 2600 + "EDITED_TAIL\n\n"


def digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def run_case(page, scenario, url):
    page.add_init_script(f"window.specReviewScenario={json.dumps(scenario)}")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url, wait_until="networkidle")
    page.wait_for_function("window.fixture?.ready || window.fixtureBootError")
    assert page.evaluate("window.fixtureBootError || ''") == ""
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    page.wait_for_function("window.fixture.completed.read === 1")
    approve = page.get_by_role("button", name="同意并继续", exact=True)

    def logs(operation):
        return page.evaluate("op=>fixture.log.filter(item=>item.operation===op)", operation)

    if scenario == "normal":
        assert "full_content=true" in logs("read")[0]["query"]

    def edit(text=EDITED):
        page.get_by_role("button", name="编辑", exact=True).click()
        editor = page.get_by_role("textbox", name="编辑 Spec 文档", exact=True)
        editor.fill(text)
        return editor

    def wait_completed(operation, count=1):
        page.wait_for_function("([op,n])=>fixture.completed[op]===n", arg=[operation, count])

    if scenario in ["get-fail", "truncated", "hash-mismatch"]:
        expect(approve).to_be_disabled()
        expect(page.get_by_role("alert")).to_be_visible()
        assert logs("approve") == []
    elif scenario == "stale":
        expect(page.get_by_role("alert")).to_be_visible()
        expect(approve).to_be_disabled()
        page.get_by_role("button", name="刷新此版本的审批", exact=True).click()
        page.wait_for_function("fixture.replacements.length === 1 && fixture.completed.read === 2")
        expect(approve).to_be_enabled()
        assert logs("approve") == []
        replacements = page.evaluate("fixture.replacements")
        assert replacements[0]["old"] == "approval-a"
        approve.click()
        wait_completed("approve")
        assert logs("approve")[0]["path"] == "/api/approvals/approval-refreshed/approve"
        assert logs("approve")[0]["body"]["response"]["documentSha256"] == page.evaluate("fixture.disk().documentSha256")
    elif scenario.startswith("save-pending"):
        expect(approve).to_be_enabled()
        editor = edit()
        page.get_by_role("button", name="保存并同意", exact=True).click()
        page.wait_for_function("fixture.log.some(item=>item.operation==='save')")
        expect(editor).to_be_disabled()
        if scenario == "save-pending-close":
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
        else:
            page.evaluate("fixture.switchId('approval-b')")
            page.wait_for_function("fixture.completed.read === 2")
            expect(approve).to_be_enabled()
        page.evaluate("fixture.release('save')")
        wait_completed("save")
        # Wait for SHA validation plus the continuation after the synthetic save.
        page.wait_for_timeout(100)
        assert logs("approve") == []
        assert page.evaluate("fixture.calls.length") == 0
        if scenario == "save-pending-close":
            page.evaluate("fixture.open()")
            expect(dialog).to_be_visible()
            page.wait_for_function("fixture.completed.read === 2")
        else:
            expect(dialog).to_be_visible()
    elif scenario == "rerender-dirty":
        expect(approve).to_be_enabled()
        editor = edit()
        page.evaluate("fixture.summary(); window.fixtureLanguage()")
        editor = page.get_by_role("textbox", name="Edit Spec document", exact=True)
        expect(editor).to_have_value(EDITED)
        assert len(logs("read")) == 1
        assert logs("approve") == []
    elif scenario == "normalized":
        expect(approve).to_be_enabled()
        edit()
        page.get_by_role("button", name="保存并同意", exact=True).click()
        wait_completed("save")
        expect(page.get_by_role("alert")).to_contain_text("保存后的格式或内容有所调整")
        expect(dialog.locator("article")).to_contain_text("SERVER_NORMALIZED")
        assert logs("approve") == []
        page.get_by_role("button", name="恢复编辑稿", exact=True).click()
        expect(page.get_by_role("textbox", name="编辑 Spec 文档", exact=True)).to_have_value(EDITED)
    elif scenario in ["save-semantic-fail", "save-conflict"]:
        expect(approve).to_be_enabled()
        editor = edit()
        page.get_by_role("button", name="保存并同意", exact=True).click()
        wait_completed("save")
        expect(page.get_by_role("alert")).to_contain_text("SYNTHETIC_SAVE_CONFLICT" if scenario == "save-conflict" else "SYNTHETIC_SAVE_LOCKED")
        expect(editor).to_have_value(EDITED)
        expect(editor).to_be_enabled()
        assert logs("approve") == []
    elif scenario == "approve-semantic-fail":
        expect(approve).to_be_enabled()
        approve.click()
        wait_completed("approve")
        expect(page.get_by_role("alert")).to_contain_text("SYNTHETIC_APPROVAL_REJECTED")
        expect(dialog).to_be_visible()
    elif scenario in ["edit", "approve-fail-once"]:
        expect(approve).to_be_enabled()
        editor = edit()
        page.get_by_role("button", name="保存并同意", exact=True).click()
        wait_completed("approve")
        assert len(logs("save")) == 1 and len(logs("approve")) == 1
        save_body = logs("save")[0]["body"]
        assert save_body["content"] == EDITED
        assert save_body["expectedDocumentSha256"] == page.evaluate("fixture.originalHash")
        approved = logs("approve")[0]
        assert approved["path"] == "/api/approvals/approval-a/approve"
        assert approved["body"]["response"] == {"answer": "", "approved": True, "documentSha256": digest(EDITED), "replaceSpecReview": True}
        if scenario == "approve-fail-once":
            expect(page.get_by_role("alert")).to_contain_text("SYNTHETIC_APPROVAL_FAILURE")
            expect(editor).to_have_value(EDITED)
            approve.click()
            wait_completed("approve", 2)
            assert len(logs("save")) == 1
            assert logs("approve")[1]["body"] == approved["body"]
    else:
        expect(approve).to_be_enabled()
        approve.click()
        wait_completed("approve")
        assert len(logs("approve")) == 1 and logs("save") == []
        assert logs("approve")[0]["body"]["response"] == {"answer": "", "approved": True, "documentSha256": page.evaluate("fixture.originalHash"), "replaceSpecReview": False}
    assert errors == [], errors
    return {"scenario": scenario, "pass": True, "readCount": len(logs("read")), "saveCount": len(logs("save")), "approvalCount": len(logs("approve")), "pageErrors": errors}


def main():
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--baseline-ref")
    args = parser.parse_args()
    if args.baseline_ref:
        OUTPUT = APP.parents[1] / "tmp/spec-review-ui-baseline"
    if not args.skip_build:
        env = dict(os.environ)
        if args.baseline_ref:
            env["V8_SPEC_REVIEW_BASELINE_REF"] = args.baseline_ref
        subprocess.run(["node", "tests/build-spec-review-ui.cjs"], cwd=APP, env=env, check=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(OUTPUT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    scenarios = args.scenario or ["normal", "edit", "save-semantic-fail", "save-conflict", "normalized", "get-fail", "truncated", "hash-mismatch", "stale", "save-pending-close", "save-pending-switch", "rerender-dirty", "approve-fail-once", "approve-semantic-fail"]
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            try:
                for scenario in scenarios:
                    context = browser.new_context(viewport={"width": 1280, "height": 1000})
                    page = context.new_page()
                    try:
                        result = run_case(page, scenario, f"http://127.0.0.1:{server.server_port}/")
                    except Exception as error:
                        result = {"scenario": scenario, "pass": False, "error": str(error)}
                        page.screenshot(path=str(OUTPUT / f"failure-{scenario}.png"))
                    results.append(result)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result_name = "result-" + "-".join(args.scenario) + ".json" if args.scenario else "result.json"
    (OUTPUT / result_name).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(not result["pass"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
