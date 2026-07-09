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

async function fetchJson(url, options = {}) {
  try {
    const response = await fetch(url, { cache: "no-store", ...options });
    const payload = await response.json().catch(() => ({}));
    return {
      ok: response.ok,
      status: response.status,
      payload,
      error: response.ok ? "" : `HTTP ${response.status}`,
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function readLocalConfig() {
  const configPath = path.join(os.homedir(), ".v8-agent-os", "config.json");
  try {
    return {
      configPath,
      data: JSON.parse(fs.readFileSync(configPath, "utf8")),
    };
  } catch (error) {
    return {
      configPath,
      data: {},
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function serviceAuthHeaders(config) {
  const secret = String(config?.systemBase?.bridge?.internalSecret || "").trim();
  if (!secret) return null;
  return {
    "x-v8-agent-os-secret": secret,
    "x-v8-agent-os-user-email": "desktop-smoke@local.v8os",
  };
}

function summarizeFeaturePacks(payload) {
  const packs = Array.isArray(payload?.packs)
    ? payload.packs
    : Array.isArray(payload?.featurePacks)
      ? payload.featurePacks
      : [];
  return {
    summary: payload?.summary || null,
    packs: packs.map((pack) => ({
      id: String(pack?.id || ""),
      status: String(pack?.status || ""),
      installed: Boolean(pack?.installed),
      restartRequired: Boolean(pack?.restartRequired),
      logRef: pack?.logRef ? String(pack.logRef) : null,
      lastError: pack?.lastError ? String(pack.lastError) : null,
    })).filter((pack) => pack.id),
  };
}

function firstFailureStage(checks) {
  const failed = Object.entries(checks).find(([, check]) => !check?.ok && !check?.skipped);
  return failed ? failed[0] : null;
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

const serviceChecks = {
  engine: await waitFor("http://127.0.0.1:9530/health"),
  admin: await waitFor("http://127.0.0.1:9528/login"),
  web: await waitFor("http://127.0.0.1:9527/chat"),
};
const config = readLocalConfig();
const headers = serviceAuthHeaders(config.data);
const engineHealth = serviceChecks.engine.ok
  ? await fetchJson("http://127.0.0.1:9530/health")
  : { ok: false, skipped: true, error: "engine_not_ready" };
const featurePackApi = serviceChecks.admin.ok && headers
  ? await fetchJson("http://127.0.0.1:9528/api/runtime-feature-packs", { headers })
  : { ok: false, skipped: true, error: headers ? "admin_not_ready" : "internal_secret_missing" };
const checks = {
  ...serviceChecks,
  engineHealth,
  featurePackApi,
};
const featurePackState = {
  engine: summarizeFeaturePacks(engineHealth.payload || {}),
  admin: summarizeFeaturePacks(featurePackApi.payload || {}),
};
const featurePackLogRefs = [...featurePackState.engine.packs, ...featurePackState.admin.packs]
  .map((pack) => pack.logRef)
  .filter(Boolean);

const payload = {
  startedAt,
  finishedAt: new Date().toISOString(),
  shellExe,
  shellPid: child.pid || null,
  ports: {
    engine: 9530,
    admin: 9528,
    web: 9527,
  },
  checks,
  featurePackState,
  featurePackLogRefs,
  failureStage: firstFailureStage(checks),
  passed: Object.values(checks).every((item) => item.ok || item.skipped),
  note: "Use the Shell tray menu to verify desktop-pet toggle and Exit V8OS cleanup in the installed app.",
};

const output = reportPath();
fs.writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(output);
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.passed ? 0 : 1);
