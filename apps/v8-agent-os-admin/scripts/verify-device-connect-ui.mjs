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
const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-device-connect-ui-"));
const port = 20000 + crypto.randomInt(1000);
const baseUrl = `http://localhost:${port}`;
const reportRoot = process.env.V8_UI_REPORT_DIR
    ? path.resolve(process.env.V8_UI_REPORT_DIR)
    : path.join(os.homedir(), ".v8-agent-os", "reports", "product_instance_gateway", new Date().toISOString().replace(/[:.]/g, "-"));
fs.mkdirSync(reportRoot, { recursive: true });
const browserCandidates = [
    process.env.V8_BROWSER_EXECUTABLE,
    process.platform === "win32" ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" : "",
    process.platform === "win32" ? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" : "",
].filter(Boolean);
const browserExecutable = browserCandidates.find((candidate) => fs.existsSync(candidate));

const serverLogs = [];
const server = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
    cwd: adminDir,
    env: {
        ...process.env,
        V8_AGENT_OS_HOME: stateRoot,
        NEXTAUTH_URL: baseUrl,
        AUTH_SECRET: "device-connect-ui-test-secret-device-connect-ui-test-secret",
        NEXTAUTH_SECRET: "device-connect-ui-test-secret-device-connect-ui-test-secret",
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

let browser;
try {
    await waitForServer();
    browser = await chromium.launch({ headless: true, ...(browserExecutable ? { executablePath: browserExecutable } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
    await page.locator("#login").fill("owner");
    await page.locator("#name").fill("Owner");
    await page.locator("#password").fill("owner-test-password");
    await page.locator("#confirmPassword").fill("owner-test-password");
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 20_000 });
    await page.goto(`${baseUrl}/admin/users`, { waitUntil: "networkidle" });

    const connectButton = page.getByRole("button", { name: "连接设备" });
    if (await connectButton.count() === 0) {
        const labels = await page.getByRole("button").allTextContents();
        throw new Error(`Connect button not found at ${page.url()}; buttons=${JSON.stringify(labels)}`);
    }
    await connectButton.click();
    await page.getByRole("dialog").waitFor();
    await page.getByText("当前 Admin URL", { exact: true }).waitFor();
    await page.getByRole("button", { name: "生成配对链接" }).click();
    const qrImage = page.getByAltText("设备配对二维码");
    await qrImage.waitFor({ timeout: 10_000 });
    const qrSource = await qrImage.getAttribute("src");
    assert.ok(qrSource?.startsWith("data:image/png;base64,"));
    assert.equal(pageErrors.length, 0, `Browser page errors: ${pageErrors.join(" | ")}`);

    const screenshot = path.join(reportRoot, "admin-device-connect-dialog.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    console.log(JSON.stringify({
        ok: true,
        screenshot,
        checks: [
            "topbar_connect_button_visible",
            "admin_url_visible",
            "pairing_ticket_created",
            "qr_code_rendered_locally",
            "no_browser_page_errors",
        ],
    }, null, 2));
} finally {
    if (browser) await browser.close();
    server.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 300));
    fs.rmSync(stateRoot, { recursive: true, force: true });
}
