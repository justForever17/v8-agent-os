import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import net from "node:net";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { verifySystemOperationsCard } from "./verify-system-operations-card.mjs";
import { ensureManagedAuthSecret } from "../../../scripts/ensure-admin-auth-secret.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const adminDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const engineDir = path.resolve(adminDir, "../v8-agent-os-engine");
const nextBin = path.join(adminDir, "node_modules", "next", "dist", "bin", "next");
const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-admin-login-interaction-"));
async function unusedPort() {
    const listener = net.createServer();
    await new Promise((resolve, reject) => { listener.once("error", reject); listener.listen(0, "127.0.0.1", resolve); });
    const selected = listener.address().port;
    await new Promise((resolve) => listener.close(resolve));
    return selected;
}
const port = await unusedPort();
const enginePort = await unusedPort();
const baseUrl = `http://127.0.0.1:${port}`;
const engineBaseUrl = `http://127.0.0.1:${enginePort}`;
const internalSecret = crypto.randomBytes(32).toString("base64url");
// Exercise the production identity router, service, database and service-proof
// boundary over HTTP. Only the external OS keychain is replaced in this fixture.
fs.writeFileSync(path.join(stateRoot, "config.json"), JSON.stringify({
    systemBase: { bridge: { engineBaseUrl: `${engineBaseUrl}/v1`, internalSecret } },
}), { mode: 0o600 });
const managed = ensureManagedAuthSecret({ stateRoot });
const password = "owner-interaction-test-password";
const browserCandidates = [
    process.env.V8_BROWSER_EXECUTABLE,
    process.platform === "win32" ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" : "",
    process.platform === "win32" ? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" : "",
    process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "",
    process.platform === "linux" ? "/usr/bin/google-chrome" : "",
    process.platform === "linux" ? "/usr/bin/chromium" : "",
    process.platform === "linux" ? "/usr/bin/chromium-browser" : "",
].filter(Boolean);
const browserExecutable = browserCandidates.find((candidate) => fs.existsSync(candidate));
const serverLogs = [];
const python = [
    process.env.V8_ADMIN_IDENTITY_PYTHON,
    path.join(engineDir, ".python", process.platform === "win32" ? "python.exe" : "bin/python3"),
    path.join(engineDir, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python"),
].find((candidate) => candidate && fs.existsSync(candidate));
assert.ok(python, "An installed Engine Python runtime is required for the Admin identity contract");
const fixtureScript = path.join(adminDir, "scripts", "admin-identity-fixture.py");
const engine = spawn(python, [fixtureScript, "--engine-root", engineDir, "--port", String(enginePort)], {
    cwd: engineDir,
    env: { ...process.env, V8_AGENT_OS_HOME: stateRoot, PYTHONUTF8: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
});
engine.stdout.on("data", (chunk) => serverLogs.push(`[identity] ${chunk}`));
engine.stderr.on("data", (chunk) => serverLogs.push(`[identity] ${chunk}`));
engine.on("error", (error) => serverLogs.push(`[identity] ${error.message}`));
const server = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
    cwd: adminDir,
    env: {
        ...process.env,
        V8_AGENT_OS_HOME: stateRoot,
        V8_ENGINE_BASE_URL: `${engineBaseUrl}/v1`,
        AUTH_URL: baseUrl,
        NEXTAUTH_URL: baseUrl,
        AUTH_SECRET: managed.secret,
        NEXTAUTH_SECRET: managed.secret,
        AUTH_TRUST_HOST: "true",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
});
server.stdout.on("data", (chunk) => serverLogs.push(String(chunk)));
server.stderr.on("data", (chunk) => serverLogs.push(String(chunk)));
server.on("error", (error) => serverLogs.push(`[admin] ${error.message}`));

async function waitForServer() {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
        if (server.exitCode !== null || engine.exitCode !== null) break;
        try {
            const identity = await fetch(`${engineBaseUrl}/readyz`, { signal: AbortSignal.timeout(1_000) });
            const ready = await identity.json();
            const response = await fetch(`${baseUrl}/login`, { signal: AbortSignal.timeout(2_000) });
            if (ready.pid === engine.pid && response.ok && (await response.text()).includes('id="login"')) return;
        } catch {}
        await new Promise((resolve) => setTimeout(resolve, 250));
    }
    let logs = serverLogs.join("").slice(-8_000);
    for (const secret of [internalSecret, managed.secret, password]) logs = logs.replaceAll(secret, "[redacted]");
    throw new Error(`Admin identity fixture did not become ready:\n${logs}`);
}

async function stopOwnedProcess(child) {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
    const exited = new Promise((resolve) => child.once("exit", resolve));
    child.kill("SIGTERM");
    const timer = setTimeout(() => child.kill("SIGKILL"), 5_000);
    try { await exited; } finally { clearTimeout(timer); }
}

async function assertFocused(page, selector) {
    assert.equal(
        await page.locator(selector).evaluate((element) => document.activeElement === element),
        true,
        `${selector} should own document focus after pointer interaction`,
    );
}

async function clickDecoration(page, decorationSelector, inputSelector) {
    const box = await page.locator(decorationSelector).boundingBox();
    assert.ok(box, `${decorationSelector} should have a visible bounding box`);
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await assertFocused(page, inputSelector);
}

let browser;
try {
    await waitForServer();
    const unauthorized = await fetch(`${engineBaseUrl}/v1/client-identity/owner`);
    assert.equal(unauthorized.status, 401, "identity management requires the service proof");
    browser = await chromium.launch({
        headless: true,
        ...(browserExecutable ? { executablePath: browserExecutable } : {}),
    });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
    const loginField = page.locator("#login");
    assert.equal(
        await loginField.evaluate((element) => element.autofocus === true),
        true,
        "#login should declare the autofocus contract",
    );
    await loginField.click();
    await assertFocused(page, "#login");
    await clickDecoration(page, '[data-v8os-input-decoration="login"]', "#login");

    const fields = [
        ["#login", "existing-owner"],
        ["#name", "Existing Owner"],
        ["#password", password],
        ["#confirmPassword", password],
    ];
    for (const [selector, value] of fields) {
        const field = page.locator(selector);
        await field.click();
        await assertFocused(page, selector);
        await field.fill(value);
        assert.equal(await field.inputValue(), value);
    }
    await clickDecoration(page, '[data-v8os-input-decoration="password"]', "#password");

    const bootstrap = await fetch(`${baseUrl}/api/auth/bootstrap`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "existing-owner", name: "Existing Owner", password }),
    });
    assert.equal(bootstrap.status, 200, JSON.stringify(await bootstrap.json().catch(() => ({}))));

    await page.locator('button[type="submit"]').click();
    await page.locator("#name").waitFor({ state: "detached" });
    assert.equal(await page.locator("#confirmPassword").count(), 0);
    assert.equal(await page.locator("#login").inputValue(), "existing-owner");
    assert.equal(await page.locator("#password").inputValue(), password);
    assert.equal(await page.locator("#password").getAttribute("autocomplete"), "current-password");

    await page.locator("#password").fill("incorrect-fixture-password");
    await page.locator('button[type="submit"]').click();
    await page.getByRole("alert").waitFor();
    assert.equal(new URL(page.url()).pathname, "/login", "wrong credentials must not create an Admin session");
    await page.locator("#password").fill(password);

    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 20_000 });
    assert.equal(pageErrors.length, 0, `Browser page errors: ${pageErrors.join(" | ")}`);
    await page.evaluate(() => {
        localStorage.setItem("v8-admin-debug-mode", "true");
        localStorage.setItem("v8-admin-sidebar-collapsed", "true");
    });
    await verifySystemOperationsCard(page, baseUrl);
    assert.equal(pageErrors.length, 0, `Browser page errors: ${pageErrors.join(" | ")}`);
    const receipt = JSON.parse(fs.readFileSync(path.join(stateRoot, "identity-http-receipt.json"), "utf8"));
    for (const route of ["POST /v1/client-identity/bootstrap 200", "POST /v1/client-identity/bootstrap 409",
        "POST /v1/client-identity/verify-credentials 401", "POST /v1/client-identity/verify-credentials 200"]) {
        assert.ok(receipt[route] >= 1, `Production Engine identity route was exercised: ${route}`);
    }

    console.log(JSON.stringify({
        ok: true,
        checks: [
            "login_autofocus_contract",
            "input_center_mouse_focus",
            "decorative_icon_click_focus",
            "stale_owner_conflict_switches_to_login",
            "existing_owner_credentials_sign_in",
            "engine_identity_service_proof_and_wrong_password_rejected",
            "engine_identity_http_bootstrap_conflict_and_login_receipts",
            "no_browser_page_errors",
            "system_operations_card_fake_os_boundary_identity_validation_password_clear_and_reload",
            "persisted_debug_and_sidebar_preferences_hydrate_without_page_errors",
        ],
    }, null, 2));
} finally {
    if (browser) await browser.close();
    await Promise.all([stopOwnedProcess(server), stopOwnedProcess(engine)]);
    fs.rmSync(stateRoot, { recursive: true, force: true });
}
