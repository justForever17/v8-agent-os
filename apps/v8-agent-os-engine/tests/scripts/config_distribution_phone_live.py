"""Actual Phone component + production PhoneTransport + paired bearer + real gateway.

The browser substitutes only the native session container/storage and shell. HTTP
responses are exclusively production Engine responses, through a transparent local
reverse proxy. It never manufactures a principal or a configuration receipt.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import threading
import time

import httpx
from playwright.sync_api import sync_playwright, expect


def phone_ui_audit(*, root, seed, ui_directory, request, start, stop, tokens, wait_job):
    observed = []
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_): pass
        def proxy(self):
            pieces = self.path.split("/", 2)
            if len(pieces) < 3 or pieces[1] not in seed or not pieces[2].startswith(("api/client/", "v1/")):
                self.send_error(404); return
            name, path = pieces[1], "/" + pieces[2]
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            headers = {key: value for key, value in self.headers.items() if key.lower() in {"authorization", "content-type"}}
            try:
                with httpx.Client(timeout=25, trust_env=False) as client:
                    response = client.request(self.command, f'http://127.0.0.1:{seed[name]["gatewayPort"]}' + path, headers=headers, content=body)
                # Record route/status only. Credentials, pairing codes and bodies stay in memory.
                observed.append({"device": name, "path": path, "status": response.status_code,
                                 "gateway": bool(response.headers.get("x-v8-request-id"))})
                self.send_response(response.status_code)
                for key in ("content-type", "x-v8-auth-stage", "x-v8-engine-now", "x-v8-request-id"):
                    if key in response.headers: self.send_header(key, response.headers[key])
                self.end_headers()
                try: self.wfile.write(response.content)
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError): pass
            except httpx.RequestError:
                try:
                    self.send_response(503); self.end_headers(); self.wfile.write(b'{"error":"fixture_gateway_offline"}')
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError): pass
        def do_GET(self):
            if self.path.split("/", 2)[1] in seed: self.proxy()
            else: super().do_GET()
        def do_POST(self): self.proxy()
        def do_PATCH(self): self.proxy()
        def do_DELETE(self): self.proxy()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ui_directory)))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    cases = []
    def passed(name):
        cases.append(name)
        print("Phone gateway UI passed: " + name, flush=True)
    review_label = "我已查看上方每台设备的差异与目标映射"
    def button(page, name): return page.get_by_role("button", name=name, exact=True)
    def pair(page, name):
        ticket = request(name, "POST", "client-identity/pairing-ticket", {"baseUrl": "https://phone-fixture.invalid", "deviceName": "Config distribution acceptance"})
        return page.evaluate("args => window.pairRealDevice(args)", {"endpoint": origin + "/" + name, "code": ticket["pairingCode"], "instanceId": ticket["instanceId"]})
    def current(page):
        result = page.evaluate("() => window.liveRead('/api/client/config-distribution')")
        assert result["status"] == 200
        job_id = result["payload"]["jobs"][0]["jobId"]
        return page.evaluate("id => window.liveRead('/api/client/config-distribution/'+id)", job_id)["payload"]
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**({"executable_path": os.environ["PHONE_TEST_BROWSER"]} if os.environ.get("PHONE_TEST_BROWSER") else {}))
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page(); page.goto(origin)
            phone = pair(page, "source")
            expect(button(page, "选择所有从设备")).to_be_visible(timeout=15000)
            button(page, "选择所有从设备").click()
            button(page, "预览 2 台设备的差异").click()
            expect(button(page, "确认应用到 2 台设备")).to_be_visible(timeout=20000)
            assert tokens("target1") == tokens("target2") == 100
            job = current(page)
            assert all(row["state"] == "prepared" and not row["approved"] for row in job["targets"])
            passed("paired_phone_gateway_component_prepare_diff_no_write")
            stop("target2")
            page.get_by_role("checkbox", name=review_label).click()
            button(page, "确认应用到 2 台设备").click()
            expect(page.get_by_text("已应用 1 / 2 台", exact=True)).to_be_visible(timeout=25000)
            expect(page.get_by_text("离线，待恢复", exact=True)).to_be_visible(timeout=25000)
            with page.expect_response(lambda response: response.url.endswith("/prepare") and response.request.method == "POST") as accepted_prepare:
                button(page, "重新准备差异").click()
            assert accepted_prepare.value.status == 200 and accepted_prepare.value.json()["intent"] == "prepare"
            # Source process dies while the reprepare intent is durable.
            stop("source"); start("target2"); start("source")
            page.reload()
            expect(page.get_by_role("button", name="模型预算与参数 ·", exact=False).first).to_be_visible(timeout=30000)
            page.get_by_role("button", name="模型预算与参数 ·", exact=False).first.click()
            expect(button(page, "确认应用到 1 台设备")).to_be_visible(timeout=30000)
            assert tokens("target1") == 777 and tokens("target2") == 100
            page.get_by_role("checkbox", name=review_label).click()
            button(page, "确认应用到 1 台设备").click()
            expect(page.get_by_text("已应用 2 / 2 台", exact=True)).to_be_visible(timeout=20000)
            assert tokens("target1") == tokens("target2") == 777
            passed("phone_reprepare_offline_restart_reconcile_and_reconfirm_only_remaining")
            page.screenshot(path=str(ui_directory / "real-gateway-completed.png"))
            # Independent target-local Broker edit must fence withdrawal.
            planned = request("target1", "POST", "config-broker/model-policy/prepare", {"governance": {"budgets": {"runMaxTokens": 999}}})
            request("target1", "POST", f'config-broker/transactions/{planned["transactionId"]}/commit', {"planDigest": planned["planDigest"]})
            button(page, "撤回已应用配置").click(); button(page, "确认撤回").click()
            expect(page.get_by_text("撤回冲突，已保留目标更新", exact=True)).to_be_visible(timeout=20000)
            expect(button(page, "重新准备差异")).to_have_attribute("aria-disabled", "true")
            assert tokens("target1") == 999 and tokens("target2") == 100
            passed("real_broker_withdrawal_conflict_preserves_local_update_and_actionable_ui")
            page.screenshot(path=str(ui_directory / "real-gateway-conflict.png"))
            button(page, "新建分发").click()
            page.get_by_role("radio", name="模型角色映射", exact=True).click()
            button(page, "选择所有从设备").click()
            # Leave target2 unmapped. It must not block target1's preview/apply.
            model = page.get_by_role("radio", name=re.compile(r"^target1:.* → chat$"))
            expect(model).to_have_count(1, timeout=15000); model.click()
            button(page, "预览 2 台设备的差异").click()
            expect(button(page, "确认应用到 1 台设备")).to_be_visible(timeout=15000)
            mapped_job = current(page)
            assert {row["state"] for row in mapped_job["targets"]} == {"prepared", "blocked"}
            page.get_by_role("checkbox", name=review_label).click(); button(page, "确认应用到 1 台设备").click()
            expect(page.get_by_text("已应用 1 / 2 台", exact=True)).to_be_visible(timeout=15000)
            button(page, "修改未确认目标映射").click()
            model2 = page.get_by_role("radio", name=re.compile(r"^target2:.* → chat$"))
            expect(model2).to_have_count(1, timeout=15000); model2.click()
            button(page, "预览 1 台设备的差异").click()
            expect(button(page, "确认应用到 1 台设备")).to_be_visible(timeout=15000)
            page.get_by_role("checkbox", name=review_label).click(); button(page, "确认应用到 1 台设备").click()
            expect(page.get_by_text("已应用 2 / 2 台", exact=True)).to_be_visible(timeout=15000)
            passed("real_role_mapping_partial_preview_repair_remaining_without_recommitting_healthy_target")
            # The actual Phone bearer cannot enter private local management.
            rejected = page.evaluate("() => window.liveRead('/v1/config-distribution')")
            assert rejected["status"] in {401, 403, 404}
            # Refresh and revocation exercised through the same real gateway/transport.
            request("source", "DELETE", "client-identity/devices/" + phone["deviceId"])
            revoked = page.evaluate("async () => {try{return (await window.liveRead('/api/client/config-distribution')).status}catch{return 401}}")
            assert revoked in {401, 403}
            passed("phone_bearer_private_route_and_revocation_rejected")
            # New authorized Phone can inspect/clean up the previous owner's job.
            pair(page, "source")
            assert any(row["state"] == "withdrawal_conflict" for row in page.evaluate("() => window.liveRead('/api/client/config-distribution')")["payload"]["jobs"])
            passed("new_paired_phone_recovers_durable_job_after_old_device_revocation")
            pair(page, "target1")
            button(page, "当前设备的本地目录映射").click()
            expect(page.get_by_role("radio", name="source → Target local project", exact=True)).to_be_visible(timeout=15000)
            page.get_by_role("radio", name="source → Target local project", exact=True).click()
            expect(button(page, "确认本地目录映射")).to_have_attribute("aria-disabled", "true")
            page.get_by_role("checkbox", name="我信任所选本地目录，并同意将它用于上方连接", exact=True).click()
            button(page, "确认本地目录映射").click()
            expect(button(page, "确认本地目录映射")).to_have_count(0, timeout=15000)
            local = page.evaluate("() => window.liveRead('/api/client/config-distribution/local-workspaces')")
            assert local["payload"]["links"][0]["localPath"] == str(root / "target1" / "local-project")
            source_links = request("source", "GET", "network-supervisor/neighbors/links")
            assert not any(row.get("workspaceBinding", {}).get("workspacePath") == str(root / "target1" / "local-project") for row in source_links["items"])
            passed("phone_target_local_project_trust_and_binding_no_source_path_copy")
            context.close(); browser.close()
        assert any(row["path"] == "/api/client/pairing/consume" and row["gateway"] and row["status"] == 200 for row in observed)
        assert any(row["path"].endswith("/confirm") and row["gateway"] and row["status"] == 200 for row in observed)
        result = {"passed": True, "cases": cases, "requests": observed,
                  "boundary": "actual PhoneTransport and component; native session container/storage replaced; real pairing/gateway/multiple Engine processes"}
        (ui_directory / "real-gateway-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return {key: result[key] for key in ("passed", "cases", "boundary")}
    finally:
        server.shutdown(); server.server_close(); worker.join(2)
