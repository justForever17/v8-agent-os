"""Production Web/Phone recovery components against a real isolated Engine API.

Requires the separately seeded /fixture/conversations proxy. No production
credentials or user state are used. Phone native storage/Alert remain adapters.
"""
from __future__ import annotations
import argparse
import json
import urllib.error
import urllib.request
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default="http://127.0.0.1:19536")
    parser.add_argument("--web", default="http://127.0.0.1:19647")
    parser.add_argument("--phone", default="http://127.0.0.1:19648")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    servers = []
    if args.serve:
        root = Path(__file__).resolve().parents[1]
        for url, directory in [(args.web, ".recovery-ui"), (args.phone, ".recovery-phone-ui")]:
            server = ThreadingHTTPServer(("127.0.0.1", int(url.rsplit(":", 1)[1])), functools.partial(SimpleHTTPRequestHandler, directory=str(root / directory)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
    def request(path, method="GET", body=None, phone=False):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        prefix = "/fixture/phone/conversations/" if phone else "/fixture/conversations/"
        req = urllib.request.Request(args.engine + prefix + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    _, source = request("source/turns?limit=10")
    last = source["messages"][-1]
    status, branch = request("source/branches", "POST", {"turnId": last["turnId"], "expectedMessageVersion": last["version"], "expectedTranscriptRevision": source["transcriptRevision"]})
    assert status == 200, branch
    session_id = branch["sessionId"]
    def current():
        status, payload = request(session_id + "/turns?limit=10")
        assert status == 200, payload
        return payload
    initial = current()
    assistant_id = initial["messages"][-1]["id"]
    user_id = initial["messages"][0]["id"]
    mutations = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        web = browser.new_page(viewport={"width": 1180, "height": 900})
        phone = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        def bridge(route, phone_mode):
            path = route.request.url.split("/conversations/s", 1)[-1]
            if route.request.method == "GET":
                status, payload = request(session_id + "/history", phone=phone_mode)
                assert all(message.get("turnId") for message in payload.get("messages", [])), "full history must keep action anchors"
            else:
                body = route.request.post_data_json
                status, payload = request(session_id + path, route.request.method, body, phone=phone_mode)
                mutations.append({"method": route.request.method, "path": path, "status": status, "body": body, "result": payload})
            route.fulfill(status=status, json=payload)
        def handler(phone_mode):
            def handle(route):
                return bridge(route, phone_mode)
            return handle
        for page, phone_mode in [(web, False), (phone, True)]:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/conversations/**", handler(phone_mode))
            page.route("**/api/client/conversations/**", handler(phone_mode))
        web.goto(args.web); phone.goto(args.phone)
        web.wait_for_load_state("networkidle"); phone.wait_for_load_state("networkidle")
        w = web.locator(f'[data-message-id="{assistant_id}"]')
        p = phone.locator(f'[data-message-id="{assistant_id}"]')
        p.get_by_role("button", name="Edit message", exact=True).click()
        p.get_by_role("textbox", name="Edit message").fill("Phone unsaved correction")
        w.get_by_role("button", name="Edit message", exact=True).click()
        text = "1. Canonical edit\n\n   exact Unicode 中文 👩🏽‍💻\n"
        w.get_by_role("textbox", name="Edit message").fill(text)
        w.get_by_role("button", name="Save", exact=True).click()
        expect(w.get_by_text("Edited by user", exact=True)).to_be_visible()
        after_web = current()
        assert after_web["messages"][-1]["content"] == text
        assert after_web["contextEpoch"] > initial["contextEpoch"]
        p.get_by_role("button", name="Save", exact=True).click()
        expect(p.get_by_text("This conversation changed elsewhere.", exact=False)).to_be_visible()
        expect(p.get_by_role("textbox", name="Edit message")).to_have_value("Phone unsaved correction")
        assert len(mutations) == 1  # stale Phone did not send a destructive write
        p.get_by_role("button", name="Compared; use current version").click()
        p.get_by_role("button", name="Save", exact=True).click()
        expect(p.get_by_text("Edited by user", exact=True)).to_be_visible()
        web.reload()
        expect(w.get_by_text("Phone unsaved correction", exact=True)).to_be_visible()
        phone_before_branch = current()
        p.get_by_role("button", name="Branch from this turn").click()
        phone.wait_for_function("Boolean(window.lastNavigation)")
        phone_branch_id = phone.evaluate("window.lastNavigation").split("id=")[1]
        _, phone_branch = request(phone_branch_id + "/history", phone=True)
        assert [message["content"] for message in phone_branch["messages"]] == [message["content"] for message in phone_before_branch["messages"]]
        assert current()["messages"] == phone_before_branch["messages"]
        before_branch = current()
        user = web.locator(f'[data-message-id="{user_id}"]')
        user.get_by_role("button", name="Edit message", exact=True).click()
        user.get_by_role("textbox", name="Edit message").fill("New branch premise")
        user.get_by_role("button", name="Edit in a new branch").click()
        web.wait_for_function("Boolean(window.lastNavigation)")
        new_id = web.evaluate("window.lastNavigation").split("id=")[1]
        _, child = request(new_id + "/turns?limit=10")
        assert [message["content"] for message in child["messages"]] == ["New branch premise"]
        assert current()["messages"] == before_branch["messages"]
        # Explicit truncation is atomic and visible on the other client on refresh.
        user.get_by_role("button", name="Edit message", exact=True).click()
        user.get_by_role("textbox", name="Edit message").fill("Current route replacement")
        user.get_by_role("button", name="Replace current route").click()
        web.get_by_role("dialog").get_by_role("button", name="Replace current route").click()
        expect(web.locator(f'[data-message-id="{assistant_id}"]')).to_have_count(0)
        phone.reload()
        expect(phone.locator(f'[data-message-id="{assistant_id}"]')).to_have_count(0)
        final = current()
        _, history = request(session_id + "/history")
        _, snapshot = request(session_id + "/snapshot")
        assert len(final["messages"]) == 1
        assert history["transcriptRevision"] == final["transcriptRevision"] == snapshot["transcriptRevision"]
        assert history["contextEpoch"] == final["contextEpoch"] == snapshot["contextEpoch"]
        assert not errors, errors
        web.screenshot(path=str(args.evidence / "engine-web.png"), full_page=True)
        phone.screenshot(path=str(args.evidence / "engine-phone.png"), full_page=True)
        (args.evidence / "engine-component-results.json").write_text(json.dumps({
            "layer": "real isolated main.app HTTP/SQLite and production recovery components; Phone uses authenticated Engine direct client dispatcher; fixture/native adapters; no full Admin login or provider",
            "sessionId": session_id, "branchId": new_id, "phoneBranchId": phone_branch_id,
            "initialIdentity": {k: initial[k] for k in ["transcriptRevision", "contextEpoch"]},
            "finalIdentity": {k: final[k] for k in ["transcriptRevision", "contextEpoch"]},
            "checks": ["web_edit_exact", "phone_stale_draft_preserved", "phone_explicit_revision", "phone_direct_branch", "reload_cross_client",
                       "branch_source_unchanged", "historical_branch_truncates_descendants", "explicit_truncate_cross_client", "history_snapshot_versions"],
            "mutations": mutations, "browserErrors": errors,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
    print("Real isolated Engine / Web / Phone recovery component integration: passed")
    for server in servers:
        server.shutdown(); server.server_close()

if __name__ == "__main__":
    main()
