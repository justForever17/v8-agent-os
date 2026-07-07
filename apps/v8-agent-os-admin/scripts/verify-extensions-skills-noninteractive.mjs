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
const buildId = path.join(adminDir, ".next", "BUILD_ID");
assert.ok(fs.existsSync(nextBin), "Next.js is not installed");
assert.ok(fs.existsSync(buildId), "Run `npm run build` before this verification");

const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-extensions-skills-noninteractive-"));
const port = 22000 + crypto.randomInt(1000);
const baseUrl = `http://127.0.0.1:${port}`;
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
        AUTH_SECRET: "extensions-skills-test-secret-extensions-skills-test-secret",
        NEXTAUTH_SECRET: "extensions-skills-test-secret-extensions-skills-test-secret",
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

function jsonFulfill(route, payload, status = 200) {
    return route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(payload),
    });
}

function extensionsConfigEnvelope() {
    return {
        domain: "extensions",
        title: "Extensions",
        summary: "Test extensions config",
        data: {
            prefilterPolicy: {
                enabled: false,
                skills: {
                    stage1Enabled: true,
                    stage1TopK: 20,
                    llmEnabled: false,
                    stage2TopK: 5,
                    llmTimeoutSeconds: 5,
                },
                mcp: {
                    stage1Enabled: true,
                    stage1TopK: 20,
                    llmEnabled: false,
                    stage2TopK: 5,
                    llmTimeoutSeconds: 5,
                },
            },
            modelBindings: {},
        },
        source: "test",
        savePath: "test",
        reloadRequired: false,
        warnings: [],
        advancedFields: [],
    };
}

function extensionsCatalog() {
    return {
        startupState: "ready",
        snapshotFreshness: "live",
        summary: {
            skillCount: 0,
            mcpServerCount: 0,
            connectedMcpServerCount: 0,
            mcpToolCount: 0,
        },
        skills: {
            rootDescriptors: [],
            items: [],
        },
        mcp: {
            servers: [],
        },
    };
}

let browser;
try {
    await waitForServer();
    browser = await chromium.launch({ headless: true, ...(browserExecutable ? { executablePath: browserExecutable } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const pageErrors = [];
    const installRequests = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
    await page.locator("#login").fill("owner");
    await page.locator("#name").fill("Owner");
    await page.locator("#password").fill("owner-test-password");
    await page.locator("#confirmPassword").fill("owner-test-password");
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 20_000 });

    await page.route("**/api/extensions/health", (route) => jsonFulfill(route, {
        runtime: {
            startupState: "ready",
            snapshotFreshness: "live",
        },
        mcp: {
            statusBreakdown: {
                connected: 0,
                disabled: 0,
                error: 0,
            },
        },
    }));
    await page.route("**/api/config-registry/extensions", (route) => jsonFulfill(route, extensionsConfigEnvelope()));
    await page.route("**/api/models", (route) => jsonFulfill(route, []));
    await page.route("**/api/skills/safety/reviews?**", (route) => jsonFulfill(route, { items: [] }));
    await page.route("**/api/extensions/catalog", (route) => jsonFulfill(route, extensionsCatalog()));
    await page.route("**/api/skills/install/command", async (route) => {
        const request = route.request();
        assert.equal(request.method(), "POST");
        const body = JSON.parse(request.postData() || "{}");
        installRequests.push(body);
        return jsonFulfill(route, {
            status: "success",
            source: "signerlabs/ShipSwift",
            targetRoot: "~/.agents/skills",
            installed: [{ name: "ShipSwift", folder: "ShipSwift", path: "~/.agents/skills/ShipSwift" }],
            skipped: [],
            conflicts: [],
            warnings: [
                "未检测到 `--yes/-y`，已自动按非交互模式执行 Skills 安装。",
                "未检测到 `-g/--global`，已自动按全局安装写入 `~/.agents/skills`。",
            ],
            normalizedCommand: "npx --yes skills add signerlabs/ShipSwift -g",
        });
    });

    await page.goto(`${baseUrl}/admin/extensions`, { waitUntil: "networkidle" });
    const installInput = page.getByPlaceholder("npx --yes skills add https://github.com/vercel-labs/skills -g --skill find-skills");
    try {
        await installInput.waitFor({ timeout: 10_000 });
    } catch (error) {
        const placeholders = await page.locator("input").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("placeholder") || "").filter(Boolean));
        const buttons = await page.getByRole("button").allTextContents();
        const bodyText = await page.locator("body").textContent().catch(() => "");
        throw new Error([
            `Skills install input not found at ${page.url()}`,
            `title=${await page.title()}`,
            `placeholders=${JSON.stringify(placeholders)}`,
            `buttons=${JSON.stringify(buttons)}`,
            `pageErrors=${JSON.stringify(pageErrors)}`,
            `body=${JSON.stringify((bodyText || "").slice(0, 1000))}`,
            error instanceof Error ? error.message : String(error),
        ].join("\n"));
    }
    await installInput.fill("npx skills add signerlabs/ShipSwift");

    const installResponsePromise = page.waitForResponse((response) => (
        response.url().endsWith("/api/skills/install/command")
        && response.request().method() === "POST"
    ), { timeout: 10_000 });
    await page.getByRole("button", { name: /运行安装命令|Run install command/ }).click();
    const installResponse = await installResponsePromise;
    assert.equal(installResponse.status(), 200);
    const installPayload = await installResponse.json();

    assert.deepEqual(installRequests, [{ command: "npx skills add signerlabs/ShipSwift" }]);
    assert.equal(installPayload.normalizedCommand, "npx --yes skills add signerlabs/ShipSwift -g");
    assert.match(installPayload.normalizedCommand, /\s--yes\s/);
    assert.doesNotMatch(installPayload.normalizedCommand, /\bnpx\s+skills\s+add\b/);
    await page.getByRole("button", { name: /运行安装命令|Run install command/ }).waitFor({ state: "visible", timeout: 10_000 });
    assert.equal(pageErrors.length, 0, `Browser page errors: ${pageErrors.join(" | ")}`);

    console.log(JSON.stringify({
        ok: true,
        checks: [
            "admin_extensions_command_install_button_submits",
            "bare_npx_skills_add_request_returns_without_hanging",
            "normalized_command_contains_noninteractive_yes_flag",
            "install_button_recovers_after_response",
            "no_browser_page_errors",
        ],
        normalizedCommand: installPayload.normalizedCommand,
    }, null, 2));
} finally {
    if (browser) await browser.close();
    server.kill("SIGTERM");
    await new Promise((resolve) => setTimeout(resolve, 300));
    fs.rmSync(stateRoot, { recursive: true, force: true });
}
