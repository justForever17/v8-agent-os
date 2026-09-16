"""Executes the production recovery component, real IndexedDB drafts and Radix modal.

The canonical API here is a deterministic fault fixture. Engine integration is a
separate layer; this test deliberately makes no provider or proof claims.
"""
from __future__ import annotations
import argparse
import copy
import json
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:19647")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--phone", action="store_true")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    server = None
    if args.serve:
        directory = Path(__file__).resolve().parents[1] / (".recovery-phone-ui" if args.phone else ".recovery-ui")
        server = ThreadingHTTPServer(("127.0.0.1", int(args.url.rsplit(":", 1)[1])), functools.partial(SimpleHTTPRequestHandler, directory=str(directory)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
    state = {
        "sessionId": "s", "transcriptRevision": 4, "contextEpoch": 0, "busy": False,
        "messages": [
            {"id": "u1", "role": "user", "turnId": "t1", "version": 1, "status": "completed", "content": "Original request"},
            {"id": "a1", "role": "assistant", "turnId": "t1", "version": 3, "status": "completed", "content": "1. Original\n\n   Indented 👩🏽‍💻\n"},
        ],
    }
    mutations: list[dict] = []
    offline = False
    race_once = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def api(route):
            nonlocal race_once
            request = route.request
            if offline:
                route.abort("internetdisconnected")
                return
            if request.method == "GET":
                route.fulfill(json=copy.deepcopy(state))
                return
            body = request.post_data_json
            if race_once and request.method == "PATCH":
                race_once = False
                state["messages"][-1]["version"] += 1
                state["messages"][-1]["content"] = "Changed between read and write"
                state["transcriptRevision"] += 1
                state["contextEpoch"] += 1
                route.fulfill(status=409, json={"detail": {"code": "message_revision_conflict"}})
                return
            assert body["expectedTranscriptRevision"] == state["transcriptRevision"]
            mutations.append({"url": request.url, "body": body})
            if request.url.endswith("/branches"):
                route.fulfill(json={"sessionId": "child-1", "newSessionId": "child-1", "sourceSessionId": "s", "transcriptRevision": 1, "contextEpoch": 1})
                return
            message_id = request.url.rsplit("/", 1)[1]
            index = next(index for index, value in enumerate(state["messages"]) if value["id"] == message_id)
            message = state["messages"][index]
            assert body["expectedMessageVersion"] == message["version"]
            if index < len(state["messages"]) - 1 and body["tailPolicy"] != "truncate":
                route.fulfill(status=409, json={"detail": {"code": "descendants_require_branch"}})
                return
            message.update(content=body["content"], version=message["version"] + 1, editedBy="user")
            if body["tailPolicy"] == "truncate":
                state["messages"] = state["messages"][:index + 1]
            state["transcriptRevision"] += 1
            state["contextEpoch"] += 1
            route.fulfill(json={key: state[key] for key in ["sessionId", "transcriptRevision", "contextEpoch"]})

        page.route("**/api/conversations/**", api)
        page.route("**/api/client/conversations/**", api)
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        assistant = page.locator('[data-message-id="a1"]')
        assistant.get_by_role("button", name="Edit message", exact=True).click()
        editor = assistant.get_by_role("textbox", name="Edit message")
        expect(editor).to_have_value(state["messages"][1]["content"])
        draft = "1. New draft\n\n   Keep indentation 👩🏽‍💻\n"
        editor.fill(draft)
        assistant.get_by_role("button", name="Close and keep draft").click()
        page.wait_for_timeout(450)  # durable debounce, not a network assertion
        page.reload()
        assistant.get_by_role("button", name="Resume draft" if args.phone else "Edit message", exact=True).click()
        expect(editor).to_have_value(draft)
        state["messages"][1].update(content="Concurrent server value", version=4)
        state["transcriptRevision"] = 5
        state["contextEpoch"] = 1
        assistant.get_by_role("button", name="Save", exact=True).click()
        expect(assistant.get_by_text("This conversation changed elsewhere.", exact=False)).to_be_visible()
        expect(editor).to_have_value(draft)
        assert len(mutations) == 0
        assistant.get_by_role("button", name="Compared; use current version").click()
        offline = True
        page.context.set_offline(True)
        if args.phone:
            assistant.get_by_role("button", name="Save", exact=True).click()
        expect(assistant.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        expect(editor).to_have_value(draft)
        page.context.set_offline(False)
        offline = False
        if args.phone:
            assistant.get_by_role("button", name="Reconnect", exact=True).click()
            expect(assistant.get_by_role("button", name="Save", exact=True)).to_be_enabled()
        assistant.get_by_role("button", name="Save", exact=True).click()
        expect(assistant.get_by_text("Edited by user", exact=True)).to_be_visible()
        assert state["messages"][1]["content"] == draft
        assert mutations[-1]["body"]["expectedMessageVersion"] == 4
        assert mutations[-1]["body"]["expectedTranscriptRevision"] == 5
        assert mutations[-1]["body"]["tailPolicy"] == "reject"
        assistant.get_by_role("button", name="Edit message", exact=True).click()
        editor.fill("Race draft must survive")
        race_once = True
        assistant.get_by_role("button", name="Save", exact=True).click()
        expect(assistant.get_by_text("This conversation changed elsewhere.", exact=False)).to_be_visible()
        expect(editor).to_have_value("Race draft must survive")
        expect(assistant.get_by_role("button", name="Compared; use current version")).to_be_visible()
        assistant.get_by_role("button", name="Discard changes", exact=True).click()

        source_before = copy.deepcopy(state)
        user = page.locator('[data-message-id="u1"]')
        user.get_by_role("button", name="Edit message", exact=True).click()
        user.get_by_role("textbox", name="Edit message").fill("Branch premise")
        user.get_by_role("button", name="Edit in a new branch").click()
        page.wait_for_function("window.lastNavigation === '/chat?id=child-1'")
        assert state == source_before
        assert mutations[-1]["body"]["messageId"] == "u1"
        assert mutations[-1]["body"]["content"] == "Branch premise"
        user.get_by_role("button", name="Edit message", exact=True).click()
        user.get_by_role("textbox", name="Edit message").fill("Replace premise")
        user.get_by_role("button", name="Replace current route").click()
        if args.phone:
            assert "1 affected turns" in page.evaluate("window.phoneConfirm.description")
            page.evaluate("window.phoneConfirm.confirm()")
        else:
            dialog = page.get_by_role("dialog")
            expect(dialog).to_contain_text("1 affected turns")
            dialog.get_by_role("button", name="Replace current route").click()
        expect(page.locator('[data-message-id="a1"]')).to_have_count(0)
        assert mutations[-1]["body"]["tailPolicy"] == "truncate"
        assert state["messages"][0]["content"] == "Replace premise"
        # User-only failed/cancelled terminal turns still expose a branch footer.
        state["messages"][0]["status"] = "cancelled"
        page.evaluate("window.refreshFixture()")
        expect(user.get_by_role("button", name="Branch from this turn")).to_be_visible()
        state["busy"] = True
        page.evaluate("window.refreshFixture()")
        expect(user.get_by_role("button", name="Branch from this turn")).to_have_count(0)
        state["busy"] = False
        state["messages"].extend([
            {"id": "hidden-a", "role": "assistant", "turnId": "t1", "version": 1, "status": "completed", "content": "Hidden same-turn answer"},
            {"id": "hidden-u", "role": "user", "turnId": "t2", "version": 1, "status": "completed", "content": "Hidden later turn"},
        ])
        state["transcriptRevision"] += 1
        page.goto(args.url + "?around=1")
        user.get_by_role("button", name="Edit message", exact=True).click()
        expect(user.get_by_role("button", name="Edit in a new branch")).to_be_visible()
        user.get_by_role("button", name="Replace current route").click()
        if args.phone:
            page.wait_for_function("Boolean(window.phoneConfirm)")
            assert "2 affected turns" in page.evaluate("window.phoneConfirm.description")
        else:
            expect(page.get_by_role("dialog")).to_contain_text("2 affected turns")
        page.screenshot(path=str(args.evidence / ("recovery-phone.png" if args.phone else "recovery-mobile.png")), full_page=True)
        assert not errors, errors
        (args.evidence / ("phone-component-results.json" if args.phone else "component-results.json")).write_text(json.dumps({
            "layer": "production Phone component + RN Web + production draft owner with adapted native storage/Alert + API fault fixture" if args.phone else "production Web component + IndexedDB + API fault fixture",
            "passed": ["exact_markdown", "draft_reload", "conflict_preserves_draft", "explicit_rebase", "offline_no_write",
                       "branch_default_source_unchanged", "truncate_confirmation", "user_only_cancelled_branch", "active_run_gate",
                       "write_time_conflict_reloads_current", "around_window_unloaded_descendants"],
            "mutations": mutations, "browserErrors": errors,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
    print(("Phone" if args.phone else "Web") + " recovery component behavior: passed")
    if server:
        server.shutdown(); server.server_close()

if __name__ == "__main__":
    main()
