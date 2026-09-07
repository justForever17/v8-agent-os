import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright";

if (!process.argv.includes("--live")) throw new Error("Pass --live to test a running Admin UI.");
const baseUrl = process.env.V8_ADMIN_TEST_URL || "http://127.0.0.1:9528";
const login = process.env.V8_ADMIN_TEST_LOGIN;
const password = process.env.V8_ADMIN_TEST_PASSWORD;
if (!login || !password) throw new Error("Set V8_ADMIN_TEST_LOGIN and V8_ADMIN_TEST_PASSWORD; credentials are never printed.");
const output = fs.mkdtempSync(path.join(os.tmpdir(), "v8-research-browser-ui-"));
const executablePath = [process.env.V8_BROWSER_EXECUTABLE,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find((candidate) => candidate && fs.existsSync(candidate));
const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN" });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.locator("#login").fill(login);
    await page.locator("#password").fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:[/?].*)?$/, { timeout: 30_000 });

    // Exercise the real component while keeping config/browser side effects
    // in a local transport fixture. This is UI integration, not a login live.
    let effective = { webFetch: { useAgentBrowserProfile: false, agentBrowserProfileAllowlist: [] } };
    let staleReadback = true;
    const events = [];
    await page.route("**/api/config-registry/system-base*", async (route) => {
        if (route.request().method() === "POST") {
            events.push("save");
            const data = route.request().postDataJSON().data;
            assert.deepEqual(data.webFetch.agentBrowserProfileAllowlist, ["docs.example.org"]);
            if (!staleReadback) effective = data;
        } else {
            events.push("read");
        }
        await route.fulfill({ json: { domain: "system-base", data: effective, warnings: [] } });
    });
    await page.route("**/api/agent-browser/open", async (route) => {
        assert.equal(effective.webFetch.useAgentBrowserProfile, true);
        assert.equal(route.request().postDataJSON().url, "https://docs.example.org/login");
        events.push("open");
        await route.fulfill({ json: { ok: true, summary: "Browser fixture opened" } });
    });
    await page.goto(`${baseUrl}/admin/research-runtime`, { waitUntil: "domcontentloaded" });
    const panel = page.locator("[data-agent-browser-panel]");
    await panel.waitFor({ state: "visible" });
    assert.equal(await panel.locator("button").count(), 1);
    await panel.locator('input[type="url"]').fill("https://docs.example.org/login");
    events.length = 0;
    await panel.locator('button[type="submit"]').click();
    await panel.getByText(/不会打开登录窗口|could not be saved or verified/).waitFor();
    assert.deepEqual(events, ["read", "save", "read"]);
    staleReadback = false;
    events.length = 0;
    await panel.locator('button[type="submit"]').click();
    await panel.getByText("Browser fixture opened").waitFor();
    assert.deepEqual(events, ["read", "save", "read", "open"]);

    for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        await panel.scrollIntoViewIfNeeded();
        const overflow = await panel.evaluate((element) => element.scrollWidth > element.clientWidth + 1);
        assert.equal(overflow, false, `Panel must fit ${width}px viewport`);
        await panel.screenshot({ path: path.join(output, `agent-browser-${width}.png`) });
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, evidenceMode: "preview-ui-mocked-side-effects", output,
        checks: ["single-entry", "exact-host-grant", "stale-readback-blocks-open", "save-readback-open-order", "desktop-mobile-fit"] }));
} finally {
    await browser.close();
}
