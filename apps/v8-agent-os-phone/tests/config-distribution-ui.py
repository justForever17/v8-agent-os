"""Actual Phone panel + API adapter over isolated synthetic HTTP; no live Engine claim."""
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

from playwright.sync_api import sync_playwright, expect

output = Path(sys.argv[1]).resolve()
states = {}


def reset():
    for name in ("A", "B"):
        states[name] = {"jobs": [], "requests": [], "reads": 0, "online": False, "lost_confirm": False,
                        "hold_create": False, "started": threading.Event(), "release": threading.Event(),
                        "localRole": "primary", "commands": {}, "credentialReady": False, "paged": False, "inventoryReads": 0}


def peer(key, online=True, local_role="primary"):
    return {"linkId": key, "peerId": "peer-" + key, "displayName": key, "online": online,
            "localRole": local_role, "remoteRole": "companion" if local_role == "primary" else "primary"}


def templates():
    return [{"id": "model-policy", "label": "模型预算与参数", "description": "同步预算和温度。",
             "values": {"governance": {"budgets": {"runMaxTokens": 2000}}}, "roles": []},
            {"id": "model-roles", "label": "模型角色映射", "description": "选择目标本地模型。",
             "values": {"roles": {"supervisor": ""}}, "roles": [{"id": "supervisor", "label": "Supervisor"}]}]


def target(link, status):
    return {"linkId": link, "peerId": "peer-" + link, "displayName": link, "state": status,
            "diff": [{"field": "governance.budgets.runMaxTokens", "before": 1000, "after": 2000}] if status == "prepared" else [],
            "missingRequirements": [], "errorCode": "peer_unreachable" if status == "offline" else "",
            "receipt": {"transactionId": "tx-" + link, "state": "ready_to_commit"} if status == "prepared" else {}}


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

    def route(self):
        parts = self.path.split("/")
        return states[parts[2]], parts[2], "/".join(parts[6:]) if len(parts) > 6 else ""

    def do_GET(self):
        if not self.path.startswith("/fixture/"):
            return super().do_GET()
        state, authority, tail = self.route()
        if not tail:
            state["inventoryReads"] += 1
            peers = [peer("Alpha", local_role=state["localRole"]), peer("Beta", state["online"])] if authority == "A" else [peer("B-only")]
            jobs = state["jobs"]
            if state["paged"]:
                jobs = [{**row, "summary": True, "targetCount": len(row["targets"]), "targetNames": [item["displayName"] for item in row["targets"][:3]], "targets": []} for row in jobs[:20]]
            return self.send_json({"servingInstanceId": authority, "templates": templates(), "peers": peers, "jobs": copy.deepcopy(jobs), "jobsNextCursor": "20" if state["paged"] else None})
        if tail.startswith("jobs?cursor="):
            offset = int(tail.split("=")[1]); rows = state["jobs"][offset:offset+20]
            return self.send_json({"items": [{**row, "summary": True, "targets": [], "targetCount": len(row["targets"])} for row in rows], "nextCursor": str(offset+20) if offset+20 < len(state["jobs"]) else None})
        if tail.startswith("targets/"):
            link = tail.split("/")[1]
            return self.send_json({"peerId": "peer-" + link, "protocolVersion": 1, "pathPolicy": "target_local_only",
                "roles": [{"id": "supervisor", "label": "Supervisor"}], "models": [
                    {"modelRef": "target/ready", "label": "Ready model", "ready": True, "missingRequirements": []},
                    {"modelRef": "target/missing", "label": "Missing credential model", "ready": state["credentialReady"], "missingRequirements": [] if state["credentialReady"] else ["credential"]}]})
        state["reads"] += 1
        job = next(item for item in state["jobs"] if item["jobId"] == tail)
        return self.send_json(copy.deepcopy(job))

    def do_PATCH(self):
        state, _, _ = self.route()
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["requests"].append({"action": "role", "body": body})
        state["localRole"] = body["localRole"]
        self.send_json({"ok": True})

    def do_POST(self):
        state, authority, tail = self.route()
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        action = tail.split("/")[-1] if tail else "create"
        state["requests"].append({"action": action, "body": body})
        if body["commandId"] in state["commands"]:
            return self.send_json(copy.deepcopy(state["commands"][body["commandId"]]))
        if not tail:
            job = {"jobId": authority + "-job-" + str(len(state["jobs"]) + 1), "revision": 2, "planDigest": "digest-2",
                   "state": "awaiting_confirmation", "templateId": body["templateId"], "createdAt": "2026-09-17T00:00:00Z",
                   "updatedAt": "2026-09-17T00:00:00Z", "targets": [target(item["linkId"], "offline" if item["linkId"] == "Beta" and not state["online"] else "prepared") for item in body["targets"]]}
            state["jobs"].insert(0, job)
            if state["hold_create"]:
                state["started"].set()
                state["release"].wait(timeout=10)
        else:
            job = next(item for item in state["jobs"] if item["jobId"] == tail.split("/")[0])
            if body["revision"] != job["revision"] or body["planDigest"] != job["planDigest"]:
                return self.send_json({"detail": "distribution_plan_stale"}, 409)
            if action == "confirm":
                for row in job["targets"]:
                    if row["state"] == "prepared":
                        row["state"] = "committed"
                        row["receipt"] = {"transactionId": "tx-" + row["linkId"], "state": "committed", "readback": {"governance.budgets.runMaxTokens": 2000}}
                job["state"] = "completed" if all(row["state"] == "committed" for row in job["targets"]) else "partial"
            elif action == "prepare":
                for index, row in enumerate(job["targets"]):
                    if row["state"] != "committed":
                        job["targets"][index] = target(row["linkId"], "prepared" if state["online"] or row["linkId"] != "Beta" else "offline")
                job["state"] = "awaiting_confirmation"
            elif action in ("cancel", "withdraw"):
                for row in job["targets"]:
                    if action == "withdraw" and row["state"] == "committed":
                        row["state"] = "rolled_back"
                        row["receipt"].update(state="rolled_back", readback={"governance.budgets.runMaxTokens": 1000})
                    elif row["state"] != "committed":
                        row["state"] = "cancelled"
                job["state"] = "withdrawn" if action == "withdraw" else "cancelled"
            job["revision"] += 1
            job["planDigest"] = "digest-" + str(job["revision"])
            job["updatedAt"] = f"2026-09-17T00:00:{job['revision']:02d}Z"
        state["commands"][body["commandId"]] = copy.deepcopy(job)
        if action == "confirm" and state["lost_confirm"]:
            state["lost_confirm"] = False
            return self.send_json({"error": "distribution_unavailable"}, 503)
        return self.send_json(copy.deepcopy(job))


results = []
server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(output)))
threading.Thread(target=server.serve_forever, daemon=True).start()


def button(page, label):
    return page.get_by_role("button", name=label, exact=True)


def new_page(browser):
    reset()
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{server.server_port}")
    expect(page.get_by_role("checkbox", name="Alpha", exact=True)).to_be_visible()
    return context, page


def preview(page, all_targets=False):
    if all_targets:
        button(page, "选择所有从设备").click()
    else:
        page.get_by_role("checkbox", name="Alpha", exact=True).click()
    button(page, f"预览 {2 if all_targets else 1} 台设备的差异").click()
    expect(button(page, "确认应用到 1 台设备")).to_be_visible()


review_label = "我已查看上方每台设备的差异与目标映射"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": os.environ["PHONE_TEST_BROWSER"]} if os.environ.get("PHONE_TEST_BROWSER") else {}))
        context, page = new_page(browser)
        preview(page, True)
        confirm = button(page, "确认应用到 1 台设备")
        expect(confirm).to_have_attribute("aria-disabled", "true")
        expect(page.get_by_text("当前: 1000", exact=True)).to_be_visible()
        expect(page.get_by_text("拟修改为: 2000", exact=True)).to_be_visible()
        assert [r["action"] for r in states["A"]["requests"]] == ["create"]
        page.get_by_role("checkbox", name=review_label).click()
        page.screenshot(path=str(output / "prepared-phone.png"))
        confirm.click()
        expect(page.get_by_text("已应用 1 / 2 台", exact=True)).to_be_visible()
        assert [row["state"] for row in states["A"]["jobs"][0]["targets"]] == ["committed", "offline"]
        states["A"]["online"] = True
        button(page, "重新准备差异").click()
        expect(page.get_by_role("checkbox", name=review_label)).to_have_attribute("aria-checked", "false")
        page.get_by_role("checkbox", name=review_label).click()
        button(page, "确认应用到 1 台设备").click()
        expect(page.get_by_text("已应用 2 / 2 台", exact=True)).to_be_visible()
        button(page, "撤回已应用配置").click()
        assert all(row["state"] == "committed" for row in states["A"]["jobs"][0]["targets"])
        button(page, "确认撤回").click()
        expect(page.get_by_text("每次运行 token 上限: 1000", exact=True)).to_have_count(1)
        button(page, "查看 Beta 的差异与回执").click()
        expect(page.get_by_text("每次运行 token 上限: 1000", exact=True)).to_have_count(1)
        results.append("explicit_confirmation_partial_success_prepare_reconfirm_withdraw_readback")
        context.close()

        context, page = new_page(browser)
        page.get_by_role("radio", name="模型角色映射", exact=True).click()
        page.get_by_role("checkbox", name="Alpha", exact=True).click()
        missing = page.get_by_role("radio", name="Alpha: Supervisor → Missing credential model", exact=True)
        expect(missing).to_have_attribute("aria-disabled", "true")
        # Preview is allowed for an incomplete batch; applying still requires
        # an explicitly mapped, target-validated model.
        expect(button(page, "预览 1 台设备的差异")).not_to_have_attribute("aria-disabled", "true")
        states["A"]["credentialReady"] = True
        button(page, "重新读取目标能力").click()
        expect(missing).not_to_have_attribute("aria-disabled", "true")
        page.get_by_role("radio", name="Alpha: Supervisor → Ready model", exact=True).click()
        button(page, "预览 1 台设备的差异").click()
        expect(button(page, "确认应用到 1 台设备")).to_be_visible()
        assert states["A"]["requests"][0]["body"]["targets"][0]["mapping"]["models"] == {"supervisor": "target/ready"}
        results.append("target_local_missing_credential_disabled_refresh_then_ready_without_reopening")
        context.close()

        context, page = new_page(browser)
        preview(page)
        page.get_by_role("checkbox", name=review_label).click()
        states["A"]["lost_confirm"] = True
        button(page, "确认应用到 1 台设备").click()
        expect(page.get_by_role("alert")).to_be_visible()
        button(page, "确认应用到 1 台设备").click()
        expect(page.get_by_text("已应用 1 / 1 台", exact=True)).to_be_visible()
        confirms = [r["body"] for r in states["A"]["requests"] if r["action"] == "confirm"]
        assert len(confirms) == 2 and confirms[0] == confirms[1]
        results.append("ambiguous_confirm_reuses_exact_command_without_duplicate_commit")
        context.close()

        context, page = new_page(browser)
        preview(page)
        page.get_by_role("checkbox", name=review_label).click()
        current = states["A"]["jobs"][0]
        current.update(revision=3, planDigest="new-digest", updatedAt="2026-09-17T00:00:03Z")
        current["targets"][0]["diff"][0]["after"] = 3000
        expect(page.get_by_text("拟修改为: 3000", exact=True)).to_be_visible(timeout=6000)
        expect(page.get_by_role("checkbox", name=review_label)).to_have_attribute("aria-checked", "false")
        expect(button(page, "确认应用到 1 台设备")).to_have_attribute("aria-disabled", "true")
        assert [r["action"] for r in states["A"]["requests"]] == ["create"]
        page.evaluate("window.setForeground(false)")
        before = states["A"]["reads"]
        page.wait_for_timeout(2300)
        assert states["A"]["reads"] == before
        page.evaluate("window.setForeground(true)")
        expect(page.get_by_text("拟修改为: 3000", exact=True)).to_be_visible()
        page.reload()
        page.get_by_role("button", name="模型预算与参数 · 等待确认", exact=False).click()
        expect(page.get_by_role("checkbox", name=review_label)).to_have_attribute("aria-checked", "false")
        assert [r["action"] for r in states["A"]["requests"]] == ["create"]
        results.append("changed_revision_clears_confirmation_background_stops_poll_reload_never_submits")
        context.close()

        context, page = new_page(browser)
        states["A"]["hold_create"] = True
        page.get_by_role("checkbox", name="Alpha", exact=True).click()
        button(page, "预览 1 台设备的差异").click()
        assert states["A"]["started"].wait(timeout=5)
        page.evaluate("window.switchAuthority('B')")
        expect(page.get_by_role("checkbox", name="B-only", exact=True)).to_be_visible()
        states["A"]["release"].set()
        page.wait_for_timeout(300)
        expect(page.get_by_role("checkbox", name="B-only", exact=True)).to_be_visible()
        expect(page.get_by_role("checkbox", name=review_label)).to_have_count(0)
        assert not states["B"]["requests"]
        results.append("late_create_response_cannot_cross_profile_or_carry_confirmation")
        context.close()

        context, page = new_page(browser)
        button(page, "设本机为从设备").first.click()
        expect(page.get_by_role("checkbox", name="Alpha", exact=True)).to_have_attribute("aria-disabled", "true")
        button(page, "选择所有从设备").click()
        button(page, "预览 1 台设备的差异").click()
        expect(page.get_by_text("已应用 0 / 1 台", exact=True)).to_be_visible()
        created = next(r for r in states["A"]["requests"] if r["action"] == "create")
        assert [row["linkId"] for row in created["body"]["targets"]] == ["Beta"]
        button(page, "取消未应用目标").click()
        assert states["A"]["jobs"][0]["state"] != "cancelled"
        button(page, "确认取消未应用目标").click()
        assert states["A"]["jobs"][0]["state"] == "cancelled"
        results.append("role_setting_filters_batch_targets_and_cancel_requires_explicit_action")
        context.close()

        context, page = new_page(browser)
        preview(page, True)
        current = states["A"]["jobs"][0]
        current.update(state="applying", revision=3, planDigest="applying-digest", updatedAt="2026-09-17T00:00:03Z")
        current["targets"][0]["state"] = "committed"
        current["targets"][1]["state"] = "applying"
        expect(page.get_by_text("已应用 1 / 2 台", exact=True)).to_be_visible(timeout=6000)
        expect(button(page, "撤回已应用配置")).not_to_have_attribute("aria-disabled", "true")
        expect(button(page, "取消未应用目标")).not_to_have_attribute("aria-disabled", "true")
        button(page, "取消未应用目标").click()
        button(page, "确认取消未应用目标").click()
        expect(page.get_by_text("已取消", exact=True)).to_have_count(2)
        assert [row["state"] for row in current["targets"]] == ["committed", "cancelled"]
        results.append("cancel_and_withdraw_remain_available_during_applying")
        context.close()

        context, page = new_page(browser)
        rows = [{"jobId": f"large-{index}", "revision": 1, "planDigest": "scale", "intent": "prepare", "state": "awaiting_confirmation" if index == 0 else "completed",
                 "templateId": "model-policy", "createdAt": "2026-09-17T00:00:00Z", "updatedAt": "2026-09-17T00:00:00Z", "targets": [target(f"Target-{i}", "prepared") for i in range(100)]} for index in range(61)]
        states["A"].update(jobs=rows, paged=True, reads=0, inventoryReads=0)
        page.reload()
        pending = page.get_by_role("button", name="模型预算与参数 · 等待确认", exact=False)
        expect(pending).to_have_count(1)
        assert states["A"]["reads"] == 0 and states["A"]["inventoryReads"] == 1
        expect(page.get_by_role("button", name="模型预算与参数 · 全部完成", exact=False)).to_have_count(19)
        button(page, "加载更多作业").click()
        expect(page.get_by_role("button", name="模型预算与参数 · 全部完成", exact=False)).to_have_count(39)
        pending.click()
        expect(page.get_by_role("heading", name="Target-99", exact=True)).to_be_visible()
        assert states["A"]["reads"] == 1
        expect(page.get_by_text("当前: 1000", exact=True)).to_have_count(1)
        button(page, "查看 Target-99 的差异与回执").click()
        expect(page.get_by_text("当前: 1000", exact=True)).to_have_count(1)
        assert states["A"]["reads"] == 1
        button(page, "新建分发").click(); pending.click()
        assert states["A"]["reads"] == 2
        before = states["A"]["inventoryReads"]
        page.evaluate("window.setForeground(false)"); page.evaluate("window.setForeground(true)")
        expect(page.get_by_role("heading", name="Target-99", exact=True)).to_be_visible()
        page.wait_for_timeout(300)
        assert states["A"]["inventoryReads"] == before + 1 and states["A"]["reads"] <= 3
        results.append("61_job_summaries_paginate_100_targets_expand_one_detail_bounded_requests")
        context.close()
        browser.close()
finally:
    server.shutdown()
(output / "result.json").write_text(json.dumps({"passed": len(results), "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": len(results), "cases": results}, ensure_ascii=False))
