#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(url, timeoutMs = 120000) {
  const startedAt = Date.now();
  let lastError = "";
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.status >= 200 && response.status < 500) {
        return { ok: true, status: response.status };
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(900);
  }
  return { ok: false, error: lastError || "timeout" };
}

function reportPath() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "_");
  const dir = path.join(os.homedir(), ".v8-agent-os", "reports", "desktop_release", stamp);
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, "install_smoke.json");
}

const shellExe = argValue("--shell-exe") || process.env.V8OS_SHELL_EXE || "";
if (!shellExe || !fs.existsSync(shellExe)) {
  console.error("Usage: node run_desktop_install_smoke.mjs --shell-exe <installed V8 Agent OS.exe>");
  process.exit(2);
}

const startedAt = new Date().toISOString();
const child = spawn(shellExe, [], {
  detached: true,
  stdio: "ignore",
  windowsHide: true,
});
child.unref();

const checks = {
  engine: await waitFor("http://127.0.0.1:9530/health"),
  admin: await waitFor("http://127.0.0.1:9528/login"),
  web: await waitFor("http://127.0.0.1:9527/chat"),
};

const payload = {
  startedAt,
  finishedAt: new Date().toISOString(),
  shellExe,
  shellPid: child.pid || null,
  checks,
  passed: Object.values(checks).every((item) => item.ok),
  note: "Use the Shell tray menu to verify desktop-pet toggle and Exit V8OS cleanup in the installed app.",
};

const output = reportPath();
fs.writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(output);
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.passed ? 0 : 1);
