import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { chromium } from "playwright";

// Real isolated Engine + production Admin. No external provider calls.
if (!process.argv.includes("--live")) throw new Error("Pass --live to start the isolated Engine and Admin acceptance fixture.");
const adminDir = path.resolve(import.meta.dirname, "..");
const repo = path.resolve(adminDir, "../..");
const engineDir = path.join(repo, "apps/v8-agent-os-engine");
const pythonIndex = process.argv.indexOf("--python");
const python = pythonIndex >= 0 ? process.argv[pythonIndex + 1] : [
    path.join(engineDir, ".python", process.platform === "win32" ? "python.exe" : "bin/python3"),
    path.join(engineDir, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python3"),
].find((candidate) => fs.existsSync(candidate));
assert.ok(python, "Engine Python is required; pass --python with its executable path.");
const reportRoot = path.resolve(process.env.V8_UI_REPORT_DIR || path.join(repo, ".artifacts/device-connect-ui", String(Date.now())));
fs.mkdirSync(reportRoot, { recursive: true });
const stateRoot = path.join(reportRoot, "isolated-state");
fs.mkdirSync(stateRoot, { recursive: true });
assert.ok(!fs.existsSync(path.join(stateRoot, "config.json")), "Preserve previous acceptance state; choose a fresh report directory.");
const children = [], descriptors = [], checks = [];
let browser, page;
let releaseProbe = () => {};
async function freePort() {
    const server = net.createServer();
    await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
    const port = server.address().port;
    await new Promise((resolve) => server.close(resolve));
    return port;
}
const adminPort = await freePort(), enginePort = await freePort(), phonePort = await freePort();
const baseUrl = `http://127.0.0.1:${adminPort}`, engineUrl = `http://127.0.0.1:${enginePort}`, phoneUrl = `http://127.0.0.1:${phonePort}`;
const secret = crypto.randomBytes(48).toString("base64url");
fs.writeFileSync(path.join(stateRoot, "config.json"), JSON.stringify({
    systemBase: { bridge: { engineBaseUrl: `${engineUrl}/v1`, adminBaseUrl: `${baseUrl}/api`, internalSecret: secret,
        allowedOrigins: [baseUrl] }, remoteLink: { enabled: true, phoneGateway: { enabled: true, port: phonePort, publicBaseUrl: "" } } },
    networkSupervisorRuntime: { enabled: false }, mcp: { enabled: false }, cron: { jobs: [] },
}), { mode: 0o600 });
const environment = { ...process.env, V8_AGENT_OS_HOME: stateRoot, ENGINE_HOST: "127.0.0.1", ENGINE_PORT: String(enginePort),
    ENGINE_RELOAD: "0", V8_ADMIN_HOSTNAME: "127.0.0.1", V8OS_DISABLE_UPDATE_CHECK: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
function launch(name, executable, args, cwd) {
    const descriptor = fs.openSync(path.join(reportRoot, `${name}.log`), "w", 0o600);
    descriptors.push(descriptor);
    const child = spawn(executable, args, { cwd, env: environment, windowsHide: true, stdio: ["ignore", descriptor, descriptor] });
    children.push(child);
    return child;
}
async function ready(url, child) {
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline) {
        if (child.exitCode !== null) throw new Error(`Owned service exited (${child.exitCode}); inspect the isolated report.`);
        try { if ((await fetch(url, { signal: AbortSignal.timeout(1500) })).ok) return; } catch {}
        await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error("Owned service readiness timed out.");
}
function passed(name) { checks.push(name); console.log(`PASS ${name}`); }
try {
    const engine = launch("engine", python, [path.join(engineDir, "main.py")], engineDir);
    await ready(`${engineUrl}/readyz`, engine);
    const admin = launch("admin", process.execPath, [path.join(repo, "scripts/run-next-with-managed-auth.mjs"), "--app", "admin", "--mode", "start", "--port", String(adminPort)], repo);
    await ready(`${baseUrl}/api/client/instance`, admin);
    browser = await chromium.launch({ headless: true, ...(process.platform === "win32" ? { channel: "msedge" } : {}) });
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseUrl}/login`);
    await page.locator("#login").fill("owner");
    await page.locator("#name").fill("Acceptance Owner");
    const password = crypto.randomBytes(18).toString("base64url");
    await page.locator("#password").fill(password);
    await page.locator("#confirmPassword").fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 30_000 });
    const entry = page.getByRole("button", { name: "连接手机", exact: true });
    await entry.waitFor();
    const rect = await entry.boundingBox();
    assert.ok(rect.width >= 40 && rect.height >= 40, `Phone entry must have a usable hit target (${rect.width} x ${rect.height}).`);
    await entry.click({ position: { x: 3, y: rect.height / 2 } });
    const dialog = page.getByRole("dialog");
    await dialog.getByText("手机连接地址", { exact: true }).waitFor();
    await dialog.getByText(/尚未启用有效的手机连接地址/).waitFor();
    assert.ok(await dialog.getByRole("button", { name: "生成 Phone 二维码" }).isDisabled());
    assert.equal(await dialog.getByTestId("phone-pairing-address").count(), 0);
    assert.equal(await dialog.getByText("当前 Admin URL", { exact: true }).count(), 0);
    await page.screenshot({ path: path.join(reportRoot, "admin-phone-address-unconfigured.png") });
    passed("unconfigured_address_never_offers_loopback_QR_and_entry_edge_click_opens");
    const probeBarrier = new Promise((resolve) => { releaseProbe = resolve; });
    let diagnosticSnapshot;
    let probeFetched;
    const probeRead = new Promise((resolve) => { probeFetched = resolve; });
    await page.route(/\/api\/config-registry\/system-base(?:\?.*)?$/, async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        if (new URL(route.request().url()).searchParams.get("refresh") === "true") {
            probeFetched();
            await probeBarrier;
            await route.fulfill({ status: 200, contentType: "application/json", json: diagnosticSnapshot });
            return;
        }
        const response = await route.fetch();
        const payload = await response.json();
        if (payload.data) {
            diagnosticSnapshot = structuredClone(payload);
            diagnosticSnapshot.data.environmentProbe = { ...diagnosticSnapshot.data.environmentProbe, status: "ready" };
            // Force an in-flight diagnostic refresh, without replacing any
            // configuration data or the production Engine save path.
            payload.data.environmentProbe = { ...payload.data.environmentProbe, status: "refreshing" };
        }
        await route.fulfill({ response, json: payload });
    });
    await dialog.getByRole("link", { name: "设置连接地址" }).click();
    await page.waitForURL(/section=phone/);
    const addressInput = page.getByRole("textbox", { name: "网关公网地址", exact: true });
    await addressInput.waitFor();
    assert.equal(await page.locator("#phone-connection details").evaluate((element) => element.open), true);
    const configured = "https://phone.acceptance.example.invalid";
    await Promise.race([probeRead, new Promise((_, reject) => setTimeout(() => reject(new Error("Diagnostic probe was not started")), 15_000))]);
    await addressInput.fill(configured);
    const probeResponse = page.waitForResponse((response) => response.url().includes("/api/config-registry/system-base?refresh=true"));
    releaseProbe();
    await probeResponse;
    await page.waitForTimeout(200);
    assert.equal(await addressInput.inputValue(), configured, "Late diagnostics must not overwrite typed Phone settings.");
    const saveResponsePromise = page.waitForResponse((response) => response.url().includes("/api/config-registry/system-base") && response.request().method() === "POST");
    await page.locator(".admin-save-bar button").click();
    const saveResponse = await saveResponsePromise;
    assert.equal(saveResponse.request().postDataJSON().data.remoteLink.phoneGateway.publicBaseUrl, configured);
    if (!saveResponse.ok()) {
        const failure = await saveResponse.json();
        throw new Error(`Phone address save failed: HTTP ${saveResponse.status()}, ${failure.detail?.code || failure.code || failure.error || "unknown"}`);
    }
    const savedManifest = await page.evaluate(async () => {
        const response = await fetch("/api/client/link/manifest", { cache: "no-store" });
        return { status: response.status, pairing: (await response.json()).pairing };
    });
    assert.equal(savedManifest.status, 200, "Read saved Phone manifest in the authenticated browser.");
    assert.equal(savedManifest.pairing?.baseUrl, configured);
    passed("settings_deep_link_expands_and_persists_real_engine_phone_address");
    await entry.click();
    await dialog.getByTestId("phone-pairing-address").waitFor();
    assert.equal(await dialog.getByTestId("phone-pairing-address").textContent(), configured);
    const responsePromise = page.waitForResponse((response) => response.url().endsWith("/api/client/pairing/tickets") && response.request().method() === "POST");
    await dialog.getByRole("button", { name: "生成 Phone 二维码" }).click();
    const ticketResponse = await responsePromise;
    assert.ok(ticketResponse.ok(), "Engine must issue the real ticket.");
    const ticket = await ticketResponse.json();
    assert.equal(new URL(ticket.pairingUri).searchParams.get("admin"), configured);
    assert.equal(ticket.adminBaseUrl, configured);
    assert.ok(ticket.adminUrls.every((address) => address.startsWith("https://") && !address.includes("127.0.0.")));
    await dialog.getByAltText("Phone 配对二维码").waitFor();
    assert.ok((await dialog.getByAltText("Phone 配对二维码").getAttribute("src")).startsWith("data:image/png;base64,"));
    const consumeBody = { code: ticket.pairingCode, instanceId: ticket.instanceId, deviceName: "Acceptance Phone" };
    const consume = await fetch(`${phoneUrl}/api/client/pairing/consume`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(consumeBody) });
    assert.ok(consume.ok);
    const paired = await consume.json();
    assert.ok((await fetch(`${phoneUrl}/api/client/auth/me`, { headers: { authorization: `Bearer ${paired.accessToken}` } })).ok);
    const duplicate = await fetch(`${phoneUrl}/api/client/pairing/consume`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(consumeBody) });
    assert.equal(duplicate.status, 410);
    passed("display_QR_ticket_and_single_use_engine_phone_identity_agree");
    // Capture a consumed QR only; it can no longer authorize a device.
    await page.screenshot({ path: path.join(reportRoot, "admin-phone-address-configured.png") });
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 800, height: 700 });
    await entry.focus();
    await page.keyboard.press("Enter");
    await dialog.waitFor();
    passed("keyboard_entry_and_compact_viewport");
    assert.equal(errors.length, 0, "No browser page errors.");
    fs.writeFileSync(path.join(reportRoot, "result.json"), JSON.stringify({ ok: true, checks,
        evidence: "real isolated Engine + production Admin + browser; synthetic HTTPS address, no external reachability or physical Phone claim" }, null, 2));
    console.log(JSON.stringify({ ok: true, reportRoot, checks }));
} catch (error) {
    // Browser exception call logs may contain fixture session cookies.
    console.error(String(error.message || "Acceptance failed").split("\n")[0]);
    console.error(String(error.stack || "").split("\n").find((line) => line.includes("verify-device-connect-ui.mjs:"))?.trim() || "");
    process.exitCode = 1;
} finally {
    releaseProbe();
    if (page) await page.unrouteAll({ behavior: "ignoreErrors" });
    if (browser) await browser.close();
    for (const child of children.reverse()) {
        if (child.exitCode !== null || !child.pid) continue;
        if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
        else child.kill("SIGTERM");
    }
    for (const descriptor of descriptors) fs.closeSync(descriptor);
}
