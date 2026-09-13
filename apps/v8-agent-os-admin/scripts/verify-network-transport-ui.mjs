import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

// Production UI with an isolated Owner and a fixture at the HTTP boundary.
// This does not claim a real LAN peer, Linux host or provider task was executed.
const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-network-transport-ui-"));
const port = 21117;
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "-p", String(port)], {
    cwd: process.cwd(), windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, V8_AGENT_OS_HOME: stateRoot, NEXTAUTH_URL: base, AUTH_TRUST_HOST: "true", AUTH_SECRET: "isolated-network-ui-fixture-only-secret" },
});
let logs = ""; server.stdout.on("data", chunk => { logs += chunk; }); server.stderr.on("data", chunk => { logs += chunk; });
let browser;
try {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
        try { if ((await fetch(`${base}/login`)).ok) break; } catch {}
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    const executablePath = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(file => fs.existsSync(file));
    browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${base}/login`, { waitUntil: "domcontentloaded" });
    await page.locator("#login").fill("owner"); await page.locator("#name").fill("Network test");
    await page.locator("#password").fill("fixture-password-123"); await page.locator("#confirmPassword").fill("fixture-password-123");
    await page.locator('button[type="submit"]').click(); await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 20000 });
    let savedOrigin = "http://127.0.0.1:9530";
    let retried = false;
    let paired = false;
    let savedLocalWorkspace = "";
    let refreshedStatus = false;
    let holdNextLinkOne = false;
    let releaseLinkOne;
    let heldLinkOne;
    let heldLinkOneFinished;
    const heldLinkOneRequest = new Promise(resolve => { heldLinkOne = resolve; });
    const heldLinkOneResponse = new Promise(resolve => { heldLinkOneFinished = resolve; });
    const connectionInvite = JSON.stringify({ kind: "v8-peer-invitation.v1", peerId: "remote", displayName: "Worker", baseUrl: "https://v8.example.com", publicKey: Buffer.alloc(32, 1).toString("base64"), code: "ABCD2345", expiresAt: new Date(Date.now() + 180000).toISOString() });
    const failMessage = { messageId: "message_fixture", seq: 130, direction: "outbound", status: "failed", body: "Test delivery", fromNickname: "Main" };
    const oldMessages = Array.from({ length: 129 }, (_, index) => ({ messageId: `m${index + 1}`, seq: index + 1, direction: "inbound", status: "received", body: `Message ${index + 1}`, fromNickname: "Worker" }));
    oldMessages[0] = { ...oldMessages[0], direction: "outbound", status: "failed" };
    await page.route("**/api/config-registry/network-supervisor-runtime", route => route.fulfill({ json: { data: { node: { advertisedBaseUrl: savedOrigin, peerBaseUrl: savedOrigin, advertisedWsUrl: "" } } } }));
    await page.route("**/api/network-supervisor/**", async route => {
        const url = new URL(route.request().url()); const pathname = url.pathname;
        let wasHeld = false;
        let result = { items: [] };
        if (pathname.endsWith("/neighbors/setup/probe")) result = { routeReachable: route.request().postDataJSON().origin === "http://192.168.50.10:9528", peerAuthenticated: false };
        else if (pathname.endsWith("/neighbors/pairing/invitations")) result = { code: "ABCD2345", invitation: connectionInvite, expiresAt: new Date(Date.now()+180000).toISOString(), inviteId: "invite" };
        else if (pathname.endsWith("/neighbors/pairing/consume")) { assert.equal(route.request().postDataJSON().invitation, connectionInvite); paired = true; result = { ok: true }; }
        else if (pathname.endsWith("/neighbors/setup")) {
            if (route.request().method() === "POST") { savedOrigin = route.request().postDataJSON().origin; result = { ok: true, origin: savedOrigin, wsUrl: "" }; }
            else result = { warning: "advertised_address_not_shareable", suggestions: [{ kind: "lan", origin: "http://192.168.50.10:9528" }] };
        } else if (pathname.endsWith("/neighbors/status")) result = { enabled: true, node: { displayName: "Main", peerId: "local" }, discovery: {} };
        else if (pathname.endsWith("/neighbors/links")) result = { items: [
            { linkId: "link1", peerId: "remote", localNickname: "Main", remoteNickname: "Worker", localRole: "primary", remoteRole: "companion", status: "connected", capabilityTags: [] },
            { linkId: "link2", peerId: "remote2", localNickname: "Main", remoteNickname: "Second worker", localRole: "companion", remoteRole: "primary", status: "connected", capabilityTags: [], workspaceBinding: { workspacePath: savedLocalWorkspace } },
        ] };
        else if (pathname.endsWith("/timeline")) {
            if (pathname.includes("/link2/")) result = { items: [{ ...oldMessages[0], messageId: "link2-only", body: "Second device only" }], previousCursor: null };
            else {
                if (holdNextLinkOne) {
                    wasHeld = true;
                    holdNextLinkOne = false; heldLinkOne();
                    await new Promise(resolve => { releaseLinkOne = resolve; });
                }
                const all = [...oldMessages, { ...failMessage, status: retried ? "delivered" : "failed" }];
                if (refreshedStatus) all.push({ messageId: "m131", seq: 131, direction: "inbound", status: "received", body: "New latest message" });
                const before = Number(url.searchParams.get("before") || Infinity);
                const items = all.filter(item => item.seq < before).slice(-100);
                result = { items, previousCursor: items[0]?.seq > 1 ? String(items[0].seq) : null };
            }
        }
        else if (pathname.endsWith("/messages/message_fixture/retry")) { retried = true; assert.deepEqual(route.request().postDataJSON(), {}); result = { ok: true }; }
        else if (pathname.endsWith("/messages/m1/retry")) { oldMessages[0].status = "delivered"; result = { ok: true, message: oldMessages[0] }; }
        else if (pathname.endsWith("/neighbors/link2") && route.request().method() === "PATCH") { savedLocalWorkspace = route.request().postDataJSON().workspaceBinding.localWorkspacePath; result = { ok: true }; }
        else if (pathname.endsWith("/task-settings")) result = { resultWakePolicy: "inbox" };
        await route.fulfill({ json: result }).catch(error => { if (!wasHeld) throw error; });
        if (wasHeld) heldLinkOneFinished();
    });
    await page.goto(`${base}/admin/network-supervisor-runtime`, { waitUntil: "domcontentloaded" });
    await page.locator("#network-peer-origin").waitFor();
    await page.getByRole("button", { name: "LAN · http://192.168.50.10:9528", exact: true }).click();
    assert.equal(await page.locator("#network-peer-origin").inputValue(), "http://192.168.50.10:9528");
    assert.equal(await page.locator("#network-peer-ws").isVisible(), false);
    await page.getByRole("button", { name: "检查连接", exact: true }).click();
    await page.getByText("本机已能访问设备路由；完成配对后再验证身份与另一台设备的连接。", { exact: true }).waitFor();
    await page.locator("#network-peer-origin").fill("http://192.168.50.99:9528");
    await page.getByRole("button", { name: "检查连接", exact: true }).click();
    await page.getByText("设备路由不可达，请检查地址、监听/防火墙和隧道转发。", { exact: true }).waitFor();
    await page.getByRole("button", { name: "LAN · http://192.168.50.10:9528", exact: true }).click();
    await page.getByRole("button", { name: "保存地址", exact: true }).click();
    await page.getByText("地址已保存，正在执行的任务继续运行。", { exact: true }).waitFor();
    await page.locator('[data-message-id="message_fixture"]').waitFor();
    assert.equal(await page.locator('[data-message-id="m1"]').count(), 0, "first page must be latest 100, not oldest 100");
    assert.equal(await page.locator('[data-message-id]').count(), 100);
    await page.getByRole("button", { name: "加载更早消息", exact: true }).click();
    await page.locator('[data-message-id="m1"]').waitFor();
    assert.equal(await page.locator('[data-message-id]').count(), 130);
    await page.locator('[data-message-id="m1"]').getByRole("button", { name: "重试发送", exact: true }).click();
    await page.locator('[data-message-id="m1"]').getByText("对端已接收", { exact: true }).waitFor();
    await page.locator('[data-message-id="message_fixture"]').getByRole("button", { name: "重试发送", exact: true }).click();
    await page.locator('[data-message-id="message_fixture"]').getByText("对端已接收", { exact: true }).waitFor();
    assert.equal(retried, true);
    refreshedStatus = true;
    await page.locator('[data-message-id="m131"]').waitFor({ timeout: 12000 });
    assert.equal(await page.locator('[data-message-id]').count(), 131);
    assert.equal(await page.locator('[data-message-id="m1"]').count(), 1, "periodic latest must retain loaded history");
    assert.equal(await page.locator('[data-message-id="message_fixture"]').count(), 1, "status update must not duplicate message");
    holdNextLinkOne = true;
    await heldLinkOneRequest;
    await page.getByRole("button", { name: /Second worker/ }).click();
    await page.locator('[data-message-id="link2-only"]').waitFor();
    releaseLinkOne();
    await heldLinkOneResponse;
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await page.waitForFunction(() => document.querySelectorAll('[data-message-id]').length === 1);
    assert.equal(await page.locator('[data-message-id="m1"]').count(), 0, "previous device must not leak into selected timeline");
    const localWorkspace = path.join(stateRoot, "peer-workspace");
    await page.locator("#network-local-workspace").fill(localWorkspace);
    await Promise.all([
        page.waitForResponse(response => response.url().endsWith("/neighbors/links") && response.ok()),
        page.getByRole("button", { name: "保存", exact: true }).click(),
    ]);
    assert.equal(savedLocalWorkspace, localWorkspace, "companion may configure its own execution directory");
    await page.getByRole("button", { name: "生成连接码", exact: true }).click();
    await page.getByRole("textbox", { name: "复制连接邀请", exact: true }).waitFor();
    assert.equal(await page.getByRole("textbox", { name: "复制连接邀请", exact: true }).inputValue(), connectionInvite);
    await page.locator("#network-connection-invitation").fill(connectionInvite);
    await page.getByRole("button", { name: "信任并连接", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("#network-connection-invitation")?.value === "");
    assert.equal(paired, true);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#network-peer-origin").waitFor();
    await page.waitForFunction(expected => document.querySelector("#network-peer-origin")?.value === expected, savedOrigin);
    assert.equal(await page.locator("#network-peer-origin").inputValue(), savedOrigin);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, evidence: "production_browser_fixture", checks: ["single_origin", "use_suggestion", "probe_success_truth", "probe_failure", "save_reload", "failed_retry_same_message", "latest_100_and_older_page", "latest_merges_status_and_preserves_history", "late_previous_link_rejected", "companion_local_workspace", "portable_invite_without_discovery", "advanced_ws_collapsed", "no_page_errors"] }));
} catch (error) { console.error(logs.slice(-2000)); throw error; }
finally { await browser?.close(); server.kill(); /* isolated fixture retained in OS temp for diagnosis */ }
