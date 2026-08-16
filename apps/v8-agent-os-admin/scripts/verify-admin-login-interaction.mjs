import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const adminDir = process.cwd();
const nextBin = path.join(adminDir, "node_modules", "next", "dist", "bin", "next");
const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-admin-login-interaction-"));
const port = 21000 + crypto.randomInt(1000);
const baseUrl = `http://127.0.0.1:${port}`;
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
const server = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
    cwd: adminDir,
    env: {
        ...process.env,
        V8_AGENT_OS_HOME: stateRoot,
        NEXTAUTH_URL: baseUrl,
        AUTH_SECRET: "admin-login-interaction-secret-admin-login-interaction-secret",
        NEXTAUTH_SECRET: "admin-login-interaction-secret-admin-login-interaction-secret",
        AUTH_TRUST_HOST: "true",
    },
    stdio: ["ignore", "pipe", "pipe"],
});
server.stdout.on("data", (chunk) => serverLogs.push(String(chunk)));
server.stderr.on("data", (chunk) => serverLogs.push(String(chunk)));

async function waitForServer() {
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
        try {
            const response = await fetch(`${baseUrl}/api/client/instance`);
            if (response.ok) return;
        } catch {}
        await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(`Admin did not become ready:\n${serverLogs.join("")}`);
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

    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 20_000 });
    assert.equal(pageErrors.length, 0, `Browser page errors: ${pageErrors.join(" | ")}`);

    console.log(JSON.stringify({
        ok: true,
        checks: [
            "login_autofocus_contract",
            "input_center_mouse_focus",
            "decorative_icon_click_focus",
            "stale_owner_conflict_switches_to_login",
            "existing_owner_credentials_sign_in",
            "no_browser_page_errors",
        ],
    }, null, 2));
} finally {
    if (browser) await browser.close();
    server.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 300));
    fs.rmSync(stateRoot, { recursive: true, force: true });
}
