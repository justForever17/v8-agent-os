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
const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "v8-desktop-pet-settings-"));
const port = 23000 + crypto.randomInt(1000);
const baseUrl = `http://127.0.0.1:${port}`;
const screenshot = process.env.V8_UI_REPORT_DIR
  ? path.join(path.resolve(process.env.V8_UI_REPORT_DIR), "desktop-pet-settings.png")
  : path.join(os.tmpdir(), `v8-desktop-pet-settings-${Date.now()}.png`);
fs.mkdirSync(path.dirname(screenshot), { recursive: true });

const serverLogs = [];
const server = spawn(process.execPath, [nextBin, "start", "-p", String(port)], {
  cwd: adminDir,
  env: {
    ...process.env,
    V8_AGENT_OS_HOME: stateRoot,
    NEXTAUTH_URL: baseUrl,
    AUTH_SECRET: "desktop-pet-settings-test-secret-desktop-pet-settings-test",
    NEXTAUTH_SECRET: "desktop-pet-settings-test-secret-desktop-pet-settings-test",
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

const envelope = {
  domain: "desktop-pet",
  data: {
    appearance: { petScale: 1, floatAmplitude: 14, floatSpeed: 3.5 },
    eventVoice: {
      enabled: true,
      mode: "system_tts",
      customRules: [
        { event: "run.reasoning.delta", phrase: "", emotion: "thinking", speak: false },
        { event: "run.completed", phrase: "任务完成了。", emotion: "happy", speak: true },
      ],
    },
    actionTable: [
      { id: "thinking", event: "run.reasoning.delta", emotion: "thinking", spectrum: "violet" },
      { id: "completed", event: "run.completed", emotion: "happy", spectrum: "emerald_green" },
    ],
    effectSpectrum: { preset: "soft", intensity: 0.75, customGlowColor: "#66e3ff" },
  },
  source: "test",
  savePath: "test",
  reloadRequired: false,
};

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const savedPayloads = [];
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
  await page.locator("#login").fill("owner");
  await page.locator("#name").fill("Owner");
  await page.locator("#password").fill("owner-test-password");
  await page.locator("#confirmPassword").fill("owner-test-password");
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/admin(?:\?.*)?$/, { timeout: 20_000 });

  await page.route("**/api/config-registry/desktop-pet", async (route) => {
    if (route.request().method() === "POST") {
      savedPayloads.push(JSON.parse(route.request().postData() || "{}"));
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(envelope) });
  });
  await page.goto(`${baseUrl}/admin/desktop-pet`, { waitUntil: "networkidle" });

  assert.equal(await page.getByText("HTTP 404", { exact: true }).count(), 0);
  assert.ok(await page.getByRole("switch").count() >= 5, "expected capsule switches for runtime and boolean settings");
  assert.equal(await page.getByPlaceholder(/tool_start|触发词/i).count(), 0);
  await page.getByRole("combobox").filter({ hasText: "Supervisor 开始思考" }).click();
  await page.getByRole("option", { name: "工具开始执行" }).waitFor();
  await page.keyboard.press("Escape");

  const saveResponse = page.waitForResponse((response) => (
    response.url().endsWith("/api/config-registry/desktop-pet")
    && response.request().method() === "POST"
  ));
  await page.getByRole("button", { name: "保存" }).click();
  await saveResponse;
  assert.equal(savedPayloads.length, 1);
  assert.ok(savedPayloads[0].data.actionTable.every((rule) => rule.event && !("match" in rule)));
  assert.equal(pageErrors.length, 0, pageErrors.join(" | "));
  await page.screenshot({ path: screenshot, fullPage: true });
  console.log(JSON.stringify({ ok: true, screenshot, savedRuleCount: savedPayloads[0].data.actionTable.length }, null, 2));
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  await new Promise((resolve) => setTimeout(resolve, 300));
  fs.rmSync(stateRoot, { recursive: true, force: true });
}
