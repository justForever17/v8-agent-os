"""Isolated actual-component + synthetic HTTP tests; no screenshots or live Engine."""
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

from playwright.sync_api import sync_playwright, expect

output = Path(sys.argv[1]).resolve()
OLD = "\ufeff# Old requirements\r\nKeep this old version.\r\n"
NEW = "\ufeff# New requirements\r\nRead this complete new version.\r\nNEW_TAIL\r\n"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def card(identity, content):
    return {"id": identity, "approval_id": identity, "approval_kind": "spec_stage_approval", "status": "pending",
            "request": {"specId": "feature-1", "stage": "requirements", "workspacePath": "fixture-workspace",
                        "documentPath": ".v8/specs/feature-1/requirements.md", "documentSha256": sha(content)}}


state = {}


def reset(content=NEW, missing=False):
    state.clear()
    state.update(content=content, cards={"old-card": card("old-card", OLD)}, requests=[], fail_read=False,
                 truncated=False, lose_refresh=False, fail_after_refresh=False, change_after_refresh=False,
                 hold_next_read=False, read_started=threading.Event(), release_read=threading.Event(), resolved_replacement=False)
    if missing:
        state["cards"]["old-card"]["request"].pop("documentSha256")


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(json.dumps(body).encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        if self.path.endswith("/instance"):
            return self.send_json({"instanceId": "fixture-instance"})
        if self.path.startswith("/api/client/"):
            assert self.headers.get("Authorization") == "Bearer synthetic-access"
        if self.path.startswith("/api/client/approvals"):
            return self.send_json({"approvals": [c for c in state["cards"].values() if c["status"] == "pending"]})
        if self.path.startswith("/api/client/specs/"):
            assert "full_content=true" in self.path
            document = state["content"]
            if state["hold_next_read"]:
                state["hold_next_read"] = False
                state["read_started"].set()
                state["release_read"].wait(timeout=10)
            if state["fail_read"]:
                return self.send_json({"error": "synthetic document read failure"}, 503)
            return self.send_json({"stages": {"requirements": {"content": document,
                "documentSha256": sha(document), "documentPath": ".v8/specs/feature-1/requirements.md",
                "truncated": state["truncated"]}}})
        return super().do_GET()

    def do_POST(self):
        assert self.headers.get("Authorization") == "Bearer synthetic-access"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        identity, action = self.path.split("/")[-2:]
        state["requests"].append({"id": identity, "action": action, "body": body})
        requested = body.get("response", {}).get("documentSha256")
        current = state["cards"][identity]
        if action == "refresh-spec-review":
            if requested != sha(state["content"]):
                return self.send_json({"detail": {"code": "spec_approval_document_changed"}}, 409)
            current["status"] = "cancelled"
            replacement = state["cards"].setdefault("new-card", card("new-card", state["content"]))
            if state["resolved_replacement"]:
                replacement["status"] = "approved"
            if state["lose_refresh"]:
                state["lose_refresh"] = False
                self.close_connection = True
                return
            if state["fail_after_refresh"]:
                state["fail_read"] = True
            if state["change_after_refresh"]:
                state["content"] += "Another edit.\r\n"
            return self.send_json({"approval": replacement, "replacesApprovalId": identity})
        if action == "approve":
            code = "spec_approval_version_required" if not current["request"].get("documentSha256") else "spec_approval_document_changed"
            if current["request"].get("documentSha256") != sha(state["content"]) or requested != sha(state["content"]):
                return self.send_json({"detail": {"code": code}}, 409)
            time.sleep(0.15)
            current["status"] = "approved"
            return self.send_json({"ok": True})
        return self.send_json({"error": "unexpected action"}, 400)


server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(output)))
threading.Thread(target=server.serve_forever, daemon=True).start()
results = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": os.environ["PHONE_TEST_BROWSER"]} if os.environ.get("PHONE_TEST_BROWSER") else {}))

        def page_for(**options):
            reset(**options)
            context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            expect(page.get_by_text(state["content"], exact=True)).to_be_visible()
            return context, page

        def button(page, label):
            return page.get_by_text(label, exact=True).locator("..").first

        approve_text = "批准并进入下一阶段"
        # Use the actual catalog to avoid guessed labels.
        catalog = json.loads((Path(__file__).resolve().parents[1] / "src/i18n/locales/zh-CN.json").read_text(encoding="utf-8"))
        approve_text = catalog["src.screens.specapprovalscreen.approve_next"]
        comment_label = catalog["src.screens.specapprovalscreen.comment_label"]

        for missing in (False, True):
            context, page = page_for(missing=missing)
            approve = button(page, approve_text)
            expect(approve).to_have_attribute("aria-disabled", "true")
            page.get_by_label(comment_label).fill("Keep my review comment")
            button(page, "刷新审批卡").dblclick()
            expect(approve).not_to_have_attribute("aria-disabled", "true")
            assert state["cards"]["old-card"]["status"] == "cancelled"
            assert state["cards"]["new-card"]["status"] == "pending"
            assert [r["action"] for r in state["requests"]] == ["refresh-spec-review"]
            expect(page.get_by_label(comment_label)).to_have_value("Keep my review comment")
            approve.dblclick()
            expect(page.get_by_text("这张审批卡已处理。", exact=True)).to_be_visible()
            assert len([r for r in state["requests"] if r["action"] == "approve"]) == 1
            assert state["requests"][-1]["id"] == "new-card"
            assert state["requests"][-1]["body"]["response"]["documentSha256"] == sha(NEW)
            assert state["requests"][-1]["body"]["response"]["answer"] == "Keep my review comment"
            results.append({"case": "missing" if missing else "stale", "requests": list(state["requests"])})
            context.close()

        context, page = page_for(content=OLD)
        page.get_by_label(comment_label).fill("Do not drop this after a conflict")
        state["content"] = NEW
        button(page, approve_text).click()
        expect(page.get_by_role("alert")).to_contain_text("文档已更新")
        expect(page.get_by_label(comment_label)).to_have_value("Do not drop this after a conflict")
        expect(button(page, approve_text)).to_have_attribute("aria-disabled", "true")
        button(page, "重新读取文档").click()
        expect(page.get_by_text(NEW, exact=True)).to_be_visible()
        assert len(state["requests"]) == 1
        results.append({"case": "409_preserves_comment_and_requires_explicit_refresh", "requests": list(state["requests"])})
        context.close()

        for failure in ("lose_refresh", "fail_after_refresh", "change_after_refresh"):
            context, page = page_for()
            page.get_by_label(comment_label).fill("Survive refresh failure")
            state[failure] = True
            button(page, "刷新审批卡").click()
            if failure == "change_after_refresh":
                expect(page.get_by_text(NEW + "Another edit.\r\n", exact=True)).to_be_visible()
            else:
                expect(page.get_by_role("alert")).to_be_visible()
            expect(button(page, approve_text)).to_have_attribute("aria-disabled", "true")
            expect(page.get_by_label(comment_label)).to_have_value("Survive refresh failure")
            assert all(r["action"] == "refresh-spec-review" for r in state["requests"])
            if failure != "change_after_refresh":
                state["fail_read"] = False
                state["fail_after_refresh"] = False
                if failure == "lose_refresh":
                    button(page, "刷新审批卡").click()
                else:
                    button(page, "重新读取文档").click()
                expect(button(page, approve_text)).not_to_have_attribute("aria-disabled", "true")
                page.reload()
                expect(page.get_by_label(comment_label)).to_have_value("Survive refresh failure")
                expect(button(page, approve_text)).not_to_have_attribute("aria-disabled", "true")
                assert len(state["cards"]) == 2
            results.append({"case": failure, "requests": list(state["requests"])})
            context.close()

        context, page = page_for(content=OLD)
        state["truncated"] = True
        button(page, "重新读取文档").click()
        expect(button(page, approve_text)).to_have_attribute("aria-disabled", "true")
        state["truncated"] = False
        state["fail_read"] = True
        button(page, "重新读取文档").click()
        expect(page.get_by_role("alert")).to_be_visible()
        expect(button(page, approve_text)).to_have_attribute("aria-disabled", "true")
        assert state["requests"] == []
        results.append({"case": "truncated_and_read_failure_cannot_approve"})
        context.close()

        context, page = page_for()
        state["resolved_replacement"] = True
        button(page, "刷新审批卡").click()
        expect(page.get_by_text("这张审批卡已处理。", exact=True)).to_be_visible()
        assert [r["action"] for r in state["requests"]] == ["refresh-spec-review"]
        results.append({"case": "resolved_replacement_never_approved_again"})
        context.close()

        context, page = page_for(content=OLD)
        page.get_by_label(comment_label).fill("Authority A comment")
        state["hold_next_read"] = True
        button(page, "重新读取文档").click()
        assert state["read_started"].wait(timeout=5)
        state["content"] = NEW
        page.evaluate("window.showAuthority('B')")
        expect(page.get_by_text(NEW, exact=True)).to_be_visible()
        page.get_by_label(comment_label).fill("Authority B comment")
        state["release_read"].set()
        expect(page.get_by_label(comment_label)).to_have_value("Authority B comment")
        expect(page.get_by_text(NEW, exact=True)).to_be_visible()
        page.evaluate("window.showAuthority('A')")
        expect(page.get_by_label(comment_label)).to_have_value("Authority A comment")
        assert state["requests"] == []
        results.append({"case": "late_A_read_cannot_replace_B_or_its_comment"})
        context.close()

        large = "\ufeff# Large requirements\r\n" + "Complete document evidence. " * 8500 + "\r\nLARGE_DOC_TAIL\r\n"
        assert len(large) > 200000
        context, page = page_for(content=large)
        document_node = page.get_by_text("LARGE_DOC_TAIL", exact=False)
        assert document_node.text_content() == large
        button(page, "刷新审批卡").click()
        expect(button(page, approve_text)).not_to_have_attribute("aria-disabled", "true")
        button(page, approve_text).click()
        expect(page.get_by_text("这张审批卡已处理。", exact=True)).to_be_visible()
        assert state["requests"][-1]["body"]["response"]["documentSha256"] == sha(large)
        results.append({"case": "over_200k_full_document_tail_and_same_byte_hash_approval", "characters": len(large)})
        context.close()
        browser.close()
finally:
    server.shutdown()
(output / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": len(results), "cases": [r["case"] for r in results]}, ensure_ascii=False))
