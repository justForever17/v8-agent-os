#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import readinessProbe from "../../lib/readiness-probe.cjs";

const { validateReadinessResponse } = readinessProbe;

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "") : "";
}

function positiveIntegerArg(name, fallback) {
  const value = Number(argValue(name) || fallback);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function booleanArg(name, fallback) {
  const value = argValue(name).trim().toLowerCase();
  if (!value) return fallback;
  if (["1", "true", "yes", "on"].includes(value)) return true;
  if (["0", "false", "no", "off"].includes(value)) return false;
  console.error(`Invalid ${name}; expected true or false.`);
  process.exit(2);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 5_000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function waitForReadiness(kind, url, timeoutMs = 120000) {
  const startedAt = Date.now();
  let lastError = "";
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetchWithTimeout(url, {}, 3_000);
      const body = await response.text();
      const validation = validateReadinessResponse(kind, {
        ok: response.ok,
        status: response.status,
        responseUrl: response.url || url,
        expectedOrigin: new URL(url).origin,
        contentType: response.headers.get("content-type") || "",
        body,
      });
      if (validation.ok) {
        return {
          ok: true,
          status: response.status,
          marker: validation.marker || null,
          surface: validation.surface || null,
          responseUrl: response.url || url,
        };
      }
      lastError = validation.reason || `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(900);
  }
  return { ok: false, error: lastError || "timeout" };
}

function isPidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function packagedResourceRoot(shellExecutable) {
  const executableDir = path.dirname(path.resolve(shellExecutable));
  return process.platform === "darwin"
    ? path.resolve(executableDir, "..", "Resources", "v8os")
    : path.join(executableDir, "resources", "v8os");
}

function spawnPackagedShell(shellExecutable, governedStateRoot) {
  const child = spawn(shellExecutable, [], {
    detached: true,
    env: {
      ...process.env,
      V8_AGENT_OS_HOME: governedStateRoot,
    },
    stdio: "ignore",
    windowsHide: true,
  });
  child.unref();
  return child;
}

async function waitForPidExit(pid, timeoutMs = 20_000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (!isPidAlive(pid)) return true;
    await sleep(250);
  }
  return !isPidAlive(pid);
}

async function bootstrapSmokeOwner() {
  const nonce = randomUUID().replaceAll("-", "");
  const result = await fetchJson("http://127.0.0.1:9528/api/auth/bootstrap", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      login: `v8os-smoke-${nonce.slice(0, 12)}`,
      name: "V8OS Smoke Owner",
      password: `${randomUUID()}${randomUUID()}`,
    }),
    timeoutMs: 15_000,
  });
  return {
    ok: Boolean(result.ok && result.payload?.success === true),
    durationMs: result.durationMs || null,
    error: result.ok && result.payload?.success === true
      ? ""
      : safeErrorCode(result.error, "owner_bootstrap_failed"),
  };
}

async function runPackagedCli(shellExecutable, resourceRoot, args, timeoutMs = 20_000) {
  const cliPath = path.join(resourceRoot, "apps", "v8-agent-os-cli", "bin", "v8os.mjs");
  if (!fs.existsSync(cliPath)) {
    return { ok: false, error: "packaged_cli_missing" };
  }
  const child = spawn(shellExecutable, [cliPath, ...args], {
    cwd: resourceRoot,
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: "1",
      V8OS_SHELL_PACKAGED: "1",
      V8_REPO_ROOT: resourceRoot,
      V8_AGENT_OS_HOME: stateRoot,
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout = `${stdout}${chunk}`.slice(-16_384); });
  child.stderr.on("data", (chunk) => { stderr = `${stderr}${chunk}`.slice(-16_384); });
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    try { child.kill(); } catch {}
  }, timeoutMs);
  const result = await new Promise((resolve) => {
    child.once("error", (error) => resolve({ code: null, error }));
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  clearTimeout(timeout);
  return {
    ok: !timedOut && !result.error && result.code === 0,
    exitCode: Number.isInteger(result.code) ? result.code : null,
    signal: result.signal || null,
    error: timedOut
      ? "packaged_cli_timeout"
      : result.error instanceof Error
        ? result.error.message
        : result.code === 0
          ? ""
          : "packaged_cli_failed",
    stdout,
    stderr,
  };
}

async function waitForDesktopPet(descriptorPath, shellDescriptorPath, timeoutMs) {
  const startedAt = Date.now();
  let lastError = "desktop_pet_descriptor_missing";
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const descriptor = JSON.parse(fs.readFileSync(descriptorPath, "utf8"));
      const shellDescriptor = JSON.parse(fs.readFileSync(shellDescriptorPath, "utf8"));
      const pid = Number(descriptor?.pid || 0);
      const serverPid = Number(descriptor?.serverPid || 0);
      const localPort = Number(descriptor?.localPort || 0);
      const localBaseUrl = String(descriptor?.localBaseUrl || "");
      const instanceId = String(descriptor?.instanceId || "");
      const localUrl = new URL(localBaseUrl);
      const identityValid = descriptor?.managedByShell === true
        && Number.isInteger(pid) && pid > 0 && isPidAlive(pid)
        && Number.isInteger(serverPid) && serverPid > 0 && isPidAlive(serverPid)
        && Number.isInteger(localPort) && localPort > 0
        && localUrl.protocol === "http:"
        && localUrl.hostname === "127.0.0.1"
        && Number(localUrl.port) === localPort
        && instanceId.length > 0;
      if (!identityValid) {
        lastError = "desktop_pet_descriptor_invalid";
        await sleep(400);
        continue;
      }
      const health = await fetchJson(`${localBaseUrl}/api/pet/health`);
      const controlConnected = shellDescriptor?.status?.controlConnected === true
        && shellDescriptor?.status?.desktopPetProcessRunning === true;
      const healthValid = health.ok
        && health.payload?.ok === true
        && health.payload?.service === "v8-agent-os-desktop-pet"
        && health.payload?.instanceId === instanceId
        && health.payload?.pid === serverPid
        && health.payload?.port === localPort;
      if (healthValid && controlConnected) {
        return {
          ok: true,
          pid,
          serverPid,
          localPort,
          controlConnected: true,
        };
      }
      lastError = healthValid ? "desktop_pet_control_not_connected" : "desktop_pet_health_invalid";
    } catch (error) {
      lastError = error instanceof Error && error.code === "ENOENT"
        ? "desktop_pet_descriptor_missing"
        : "desktop_pet_descriptor_invalid";
    }
    await sleep(400);
  }
  return { ok: false, error: lastError };
}

function readShellSurface(descriptorPath, expectedPid) {
  try {
    const descriptor = JSON.parse(fs.readFileSync(descriptorPath, "utf8"));
    const surfaceKind = String(descriptor?.surfaceKind || "");
    const pid = Number(descriptor?.pid || 0);
    const allowedSurfaceKinds = new Set(["web", "admin", "admin-login"]);
    const ok = descriptor?.surfaceReady === true
      && allowedSurfaceKinds.has(surfaceKind)
      && pid === expectedPid
      && isPidAlive(pid);
    return {
      ok,
      pid: Number.isInteger(pid) && pid > 0 ? pid : null,
      surfaceKind: allowedSurfaceKinds.has(surfaceKind) ? surfaceKind : null,
      surfaceReadyAt: typeof descriptor?.surfaceReadyAt === "string" ? descriptor.surfaceReadyAt : null,
      error: ok ? "" : "shell_surface_not_ready",
    };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error && error.code === "ENOENT"
        ? "shell_control_descriptor_missing"
        : "shell_control_descriptor_invalid",
    };
  }
}

async function waitForShellSurface(descriptorPath, expectedPid, timeoutMs) {
  const startedAt = Date.now();
  let last = { ok: false, error: "shell_control_descriptor_missing" };
  while (Date.now() - startedAt < timeoutMs) {
    if (!isPidAlive(expectedPid)) return { ok: false, pid: expectedPid, error: "shell_process_exited" };
    last = readShellSurface(descriptorPath, expectedPid);
    if (last.ok) return last;
    await sleep(500);
  }
  return last;
}

async function fetchJson(url, options = {}) {
  const startedAt = Date.now();
  try {
    const { timeoutMs = 5_000, ...requestOptions } = options;
    const response = await fetchWithTimeout(url, requestOptions, timeoutMs);
    const payload = await response.json().catch(() => ({}));
    return {
      ok: response.ok,
      status: response.status,
      payload,
      durationMs: Date.now() - startedAt,
      error: response.ok ? "" : `HTTP ${response.status}`,
    };
  } catch (error) {
    return {
      ok: false,
      durationMs: Date.now() - startedAt,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function safeErrorCode(value, fallback) {
  const candidate = String(value || "").trim();
  return /^[a-z0-9_]{1,96}$/i.test(candidate) ? candidate : fallback;
}

function packagedPython(resourceRoot) {
  const engineRoot = path.join(resourceRoot, "apps", "v8-agent-os-engine");
  const candidates = process.platform === "win32"
    ? [path.join(engineRoot, ".python", "python.exe")]
    : [
      path.join(engineRoot, ".python", "bin", "python3"),
      path.join(engineRoot, ".python", "bin", "python"),
    ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function sanitizedProbeEnvironment(resourceRoot) {
  const sanitized = {};
  const secretLike = /(?:api[_-]?key|token|secret|password|cookie|authorization|bearer|credential)/i;
  for (const [key, value] of Object.entries(process.env)) {
    const normalizedKey = key.toUpperCase();
    if (normalizedKey === "PYTHONPATH" || normalizedKey === "PYTHONHOME" || secretLike.test(key)) continue;
    sanitized[key] = value;
  }
  sanitized.V8_REPO_ROOT = resourceRoot;
  sanitized.V8_AGENT_OS_HOME = stateRoot;
  sanitized.PYTHONNOUSERSITE = "1";
  return sanitized;
}

function terminateProcessTree(child) {
  if (!child?.pid) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
    const fallback = () => {
      try { child.kill("SIGKILL"); } catch {}
    };
    killer.once("error", fallback);
    killer.once("exit", (code) => { if (code !== 0) fallback(); });
    killer.unref();
    return;
  }
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch {
    try { child.kill("SIGKILL"); } catch {}
  }
}

function runtimeProbeSummary(payload) {
  if (!payload || typeof payload !== "object" || payload.mode !== "offline_runtime_probe") {
    return { ok: false, error: "feature_pack_probe_invalid_response" };
  }
  const summarize = (value, permittedBooleans) => {
    if (!value || typeof value !== "object") return null;
    if (!["not_installed", "installed", "failed"].includes(value.state)) return null;
    if (typeof value.checked !== "boolean" || typeof value.failClosed !== "boolean") return null;
    if (value.state === "installed" && permittedBooleans.some((key) => typeof value[key] !== "boolean")) return null;
    const summary = {
      state: value.state,
      checked: value.checked,
      failClosed: value.failClosed,
      error: value.error ? safeErrorCode(value.error, "feature_pack_probe_invalid_response") : null,
    };
    for (const key of permittedBooleans) summary[key] = typeof value[key] === "boolean" ? value[key] : false;
    return summary;
  };
  const rpa = summarize(payload.rpa, ["available", "isolated", "dryRunPassed"]);
  const image = summarize(payload.image, [
    "assetResolved",
    "cpuSessionLoaded",
    "isolated",
    "moduleOriginsVerified",
    "modelShaVerified",
  ]);
  if (!rpa || !image || typeof payload.ok !== "boolean") {
    return { ok: false, error: "feature_pack_probe_invalid_response" };
  }
  const rpaOk = rpa.checked && !rpa.error && (
    (rpa.state === "not_installed" && rpa.failClosed)
    || (rpa.state === "installed" && !rpa.failClosed && rpa.available && rpa.isolated && rpa.dryRunPassed)
  );
  const imageOk = image.checked && !image.error && (
    (image.state === "not_installed" && image.failClosed)
    || (image.state === "installed" && !image.failClosed
      && image.assetResolved && image.cpuSessionLoaded && image.isolated
      && image.moduleOriginsVerified && image.modelShaVerified)
  );
  const derivedOk = Boolean(rpaOk && imageOk);
  if (payload.ok !== derivedOk) {
    return { ok: false, error: "feature_pack_probe_invalid_response" };
  }
  return {
    ok: derivedOk,
    mode: "offline_runtime_probe",
    rpa,
    image,
    error: payload.error ? safeErrorCode(payload.error, "feature_pack_probe_invalid_response") : null,
  };
}

async function runFeaturePackRuntimeProbe(resourceRoot, engineStatus, timeoutMs) {
  const python = packagedPython(resourceRoot);
  const probePath = path.join(
    resourceRoot,
    "apps",
    "v8-agent-os-shell",
    "scripts",
    "feature_pack_runtime_probe.py",
  );
  if (!python) return { ok: false, error: "packaged_python_missing" };
  if (!fs.existsSync(probePath)) return { ok: false, error: "feature_pack_probe_missing" };

  const startedAt = Date.now();
  const child = spawn(python, ["-I", "-B", probePath], {
    cwd: resourceRoot,
    detached: process.platform !== "win32",
    env: sanitizedProbeEnvironment(resourceRoot),
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  const maxOutputBytes = 32 * 1024;
  let stdout = "";
  let outputOverflow = false;
  let finishProbe;
  let terminationFallback = null;
  const resultPromise = new Promise((resolve) => {
    let settled = false;
    finishProbe = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    child.once("error", (error) => finishProbe({ code: null, error }));
    child.once("exit", (code) => finishProbe({ code }));
  });
  const stopProbe = () => {
    terminateProcessTree(child);
    if (!terminationFallback) {
      terminationFallback = setTimeout(
        () => finishProbe({ code: null, error: new Error("probe termination timed out") }),
        3_000,
      );
    }
  };
  child.stdout.on("data", (chunk) => {
    if (outputOverflow) return;
    stdout += chunk.toString("utf8");
    if (Buffer.byteLength(stdout, "utf8") > maxOutputBytes) {
      outputOverflow = true;
      stopProbe();
    }
  });
  child.stderr.resume();
  child.stdin.on("error", () => {});
  child.stdin.end(`${JSON.stringify({ engineStatus })}\n`);
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    stopProbe();
  }, timeoutMs);
  const result = await resultPromise;
  clearTimeout(timeout);
  if (terminationFallback) clearTimeout(terminationFallback);
  const durationMs = Date.now() - startedAt;
  if (timedOut) return { ok: false, durationMs, error: "feature_pack_probe_timeout" };
  if (outputOverflow) return { ok: false, durationMs, error: "feature_pack_probe_output_limit" };
  if (result.error) return { ok: false, durationMs, error: "feature_pack_probe_spawn_failed" };
  let parsed;
  try {
    const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length !== 1) throw new Error("invalid probe output");
    parsed = JSON.parse(lines[0]);
  } catch {
    return { ok: false, durationMs, error: "feature_pack_probe_invalid_response" };
  }
  const summary = runtimeProbeSummary(parsed);
  return {
    ...summary,
    ok: result.code === 0 && summary.ok,
    durationMs,
    error: result.code === 2
      ? "feature_pack_probe_input_invalid"
      : result.code !== 0 && !summary.error
        ? "feature_pack_runtime_unhealthy"
        : summary.error,
  };
}

function readLocalConfig() {
  const configPath = path.join(stateRoot, "config.json");
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
    })).filter((pack) => pack.id),
  };
}

function hasEngineFeaturePackStatusSchema(payload) {
  if (!payload || !Number.isFinite(Date.parse(String(payload.sampledAt || "")))) return false;
  if (!Array.isArray(payload.featurePacks)) return false;
  const requiredIds = new Set(["rpa_automation", "creative_media_image_analysis"]);
  const seenIds = new Set();
  const allowedStatuses = new Set(["installed", "not_installed", "installing", "failed"]);
  for (const pack of payload.featurePacks) {
    if (!pack || typeof pack !== "object" || !requiredIds.has(String(pack.id || ""))) continue;
    if (seenIds.has(String(pack.id))) return false;
    seenIds.add(String(pack.id));
    requiredIds.delete(String(pack.id));
    if (
      !allowedStatuses.has(String(pack.status || ""))
      || typeof pack.installed !== "boolean"
      || typeof pack.restartRequired !== "boolean"
    ) return false;
  }
  return requiredIds.size === 0;
}

function engineFeaturePackStatusSummary(payload) {
  const statusById = new Map((payload?.featurePacks || []).map((pack) => [String(pack?.id || ""), pack]));
  const summary = (id) => {
    const pack = statusById.get(id);
    return {
      installed: Boolean(pack?.installed),
      restartRequired: Boolean(pack?.restartRequired),
    };
  };
  return {
    rpa: summary("rpa_automation"),
    image: summary("creative_media_image_analysis"),
  };
}

function hasFeaturePackPayloadSchema(payload) {
  if (!Array.isArray(payload?.packs)) return false;
  const summary = payload?.summary;
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
    return false;
  }
  if (!["total", "installed", "missing", "installing", "failed"]
    .every((field) => Number.isInteger(summary[field]) && summary[field] >= 0)) {
    return false;
  }

  const requiredAdminPackIds = new Set(["rpa_automation", "creative_media_image_analysis"]);
  const seenIds = new Set();
  const allowedStatuses = new Set(["installed", "not_installed", "installing", "failed"]);
  for (const pack of payload.packs) {
    const id = String(pack?.id || "");
    if (!requiredAdminPackIds.has(id)) continue;
    if (seenIds.has(id)) return false;
    seenIds.add(id);
    requiredAdminPackIds.delete(id);
    if (
      !allowedStatuses.has(String(pack?.status || ""))
      || typeof pack.installed !== "boolean"
      || typeof pack.installable !== "boolean"
      || typeof pack.restartRequired !== "boolean"
    ) return false;
  }
  return requiredAdminPackIds.size === 0;
}

function featurePackApiMatchesEngine(adminPayload, enginePayload) {
  const adminById = new Map((adminPayload?.packs || []).map((pack) => [String(pack?.id || ""), pack]));
  const engineById = new Map((enginePayload?.featurePacks || []).map((pack) => [String(pack?.id || ""), pack]));
  for (const id of ["rpa_automation", "creative_media_image_analysis"]) {
    const adminPack = adminById.get(id);
    const enginePack = engineById.get(id);
    if (!adminPack || !enginePack) return false;
    if (
      adminPack.status !== enginePack.status
      || adminPack.installed !== enginePack.installed
      || adminPack.restartRequired !== enginePack.restartRequired
    ) return false;
  }
  return true;
}

function firstFailureStage(checks) {
  const failed = Object.entries(checks).find(([, check]) => !check?.ok && !check?.skipped);
  return failed ? failed[0] : null;
}

function reportPath() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "_");
  const dir = path.join(stateRoot, "reports", "desktop_release", stamp);
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, "install_smoke.json");
}

const shellExe = argValue("--shell-exe") || process.env.V8OS_SHELL_EXE || "";
const explicitResourceRoot = argValue("--resource-root");
const serviceTimeoutMs = positiveIntegerArg("--timeout-ms", 90_000);
const startupBudgetMs = positiveIntegerArg("--startup-budget-ms", 90_000);
const featurePackSmokeEnabled = booleanArg("--feature-pack-smoke", true);
const featurePackProbeTimeoutMs = Math.min(120_000, positiveIntegerArg("--feature-pack-probe-timeout-ms", 30_000));
const stateRoot = path.resolve(
  process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os"),
);
if (!shellExe || !fs.existsSync(shellExe)) {
  console.error("Usage: node run_desktop_install_smoke.mjs --shell-exe <packaged V8 Agent OS executable>");
  process.exit(2);
}

const startedAt = new Date().toISOString();
const startedAtMs = Date.now();
const shellControlPath = path.join(stateRoot, "runtime", "shell-control.json");
const desktopPetDescriptorPath = path.join(stateRoot, "runtime", "desktop-pet.json");
const resourceRoot = explicitResourceRoot
  ? path.resolve(explicitResourceRoot)
  : packagedResourceRoot(shellExe);
if (!fs.existsSync(resourceRoot) || !fs.statSync(resourceRoot).isDirectory()) {
  console.error(`Packaged resource root is not a directory: ${resourceRoot}`);
  process.exit(2);
}
let child = spawnPackagedShell(shellExe, stateRoot);

const [engine, admin, web, initialShellSurface] = await Promise.all([
  waitForReadiness("engine", "http://127.0.0.1:9530/readyz", serviceTimeoutMs),
  waitForReadiness("admin", "http://127.0.0.1:9528/login", serviceTimeoutMs),
  waitForReadiness("web", "http://127.0.0.1:9527/chat", serviceTimeoutMs),
  waitForShellSurface(shellControlPath, child.pid || 0, serviceTimeoutMs),
]);
const rawInitialInstanceManifest = admin.ok
  ? await fetchJson("http://127.0.0.1:9528/api/client/instance", { timeoutMs: 3_000 })
  : { ok: false, error: "admin_not_ready" };
const initialInstanceManifestValid = rawInitialInstanceManifest.ok
  && rawInitialInstanceManifest.payload?.kind === "v8_instance_manifest"
  && rawInitialInstanceManifest.payload?.initialized === false;
const bootstrapSurface = {
  ok: Boolean(initialInstanceManifestValid
    && initialShellSurface.ok
    && initialShellSurface.surfaceKind === "admin-login"),
  initialized: initialInstanceManifestValid ? false : null,
  surfaceKind: initialShellSurface.surfaceKind || null,
  error: !initialInstanceManifestValid
    ? "initial_instance_manifest_invalid"
    : initialShellSurface.surfaceKind !== "admin-login"
      ? "initial_bootstrap_surface_mismatch"
      : initialShellSurface.error || "",
};

let ownerBootstrap = { ok: false, error: "initial_bootstrap_surface_unavailable" };
let shellRestart = { ok: false, error: "owner_bootstrap_unavailable" };
let shellSurface = initialShellSurface;
let rawInstanceManifest = rawInitialInstanceManifest;
if (bootstrapSurface.ok) {
  ownerBootstrap = await bootstrapSmokeOwner();
  if (ownerBootstrap.ok) {
    const initialShellPid = child.pid || 0;
    const stopped = await runPackagedCli(
      shellExe,
      resourceRoot,
      ["stop", "--only", "shell", "--json"],
      45_000,
    );
    const exited = stopped.ok && await waitForPidExit(initialShellPid);
    shellRestart = {
      ok: Boolean(stopped.ok && exited),
      stopped: Boolean(stopped.ok),
      oldProcessExited: Boolean(exited),
      error: !stopped.ok ? "initial_shell_stop_failed" : exited ? "" : "initial_shell_exit_timeout",
    };
    if (shellRestart.ok) {
      child = spawnPackagedShell(shellExe, stateRoot);
      shellSurface = await waitForShellSurface(shellControlPath, child.pid || 0, serviceTimeoutMs);
      rawInstanceManifest = await fetchJson("http://127.0.0.1:9528/api/client/instance", { timeoutMs: 3_000 });
    }
  }
}
const instanceManifestValid = rawInstanceManifest.ok
  && rawInstanceManifest.payload?.kind === "v8_instance_manifest"
  && rawInstanceManifest.payload?.initialized === true;
shellSurface.ok = Boolean(
  shellSurface.ok
  && instanceManifestValid
  && shellSurface.surfaceKind === "web",
);
shellSurface.expectedSurfaceKind = "web";
shellSurface.error = shellSurface.ok
  ? ""
  : instanceManifestValid
    ? "trusted_web_surface_mismatch"
    : "initialized_instance_manifest_invalid";
const instanceManifest = {
  ok: Boolean(instanceManifestValid),
  initialized: instanceManifestValid ? true : null,
  error: instanceManifestValid ? "" : safeErrorCode(rawInstanceManifest.error, "instance_manifest_invalid"),
};
const startupDurationMs = Date.now() - startedAtMs;
const serviceChecks = { engine, admin, web };
const desktopPetStartedAtMs = Date.now();
const desktopPetLaunch = Object.values(serviceChecks).every((item) => item.ok) && shellSurface.ok
  ? await runPackagedCli(shellExe, resourceRoot, ["start", "--only", "desktop-pet", "--mode", "start", "--json"])
  : { ok: false, error: "core_surface_not_ready" };
const desktopPet = desktopPetLaunch.ok
  ? await waitForDesktopPet(desktopPetDescriptorPath, shellControlPath, Math.min(serviceTimeoutMs, 30_000))
  : { ok: false, error: safeErrorCode(desktopPetLaunch.error, "desktop_pet_launch_failed") };
desktopPet.startupDurationMs = Date.now() - desktopPetStartedAtMs;
const config = readLocalConfig();
const headers = serviceAuthHeaders(config.data);
const featurePackEngineStartedAt = Date.now();
const rawFeaturePackEngineStatus = serviceChecks.engine.ok && headers
  ? await fetchJson("http://127.0.0.1:9530/v1/runtime-feature-packs/status", { headers, timeoutMs: 3_000 })
  : { ok: false, error: headers ? "engine_not_ready" : "internal_secret_missing" };
const featurePackEngineStatus = {
  ok: Boolean(rawFeaturePackEngineStatus.ok && hasEngineFeaturePackStatusSchema(rawFeaturePackEngineStatus.payload)),
  durationMs: Date.now() - featurePackEngineStartedAt,
  ...engineFeaturePackStatusSummary(rawFeaturePackEngineStatus.payload),
  error: rawFeaturePackEngineStatus.ok
    ? hasEngineFeaturePackStatusSchema(rawFeaturePackEngineStatus.payload)
      ? null
      : "feature_pack_engine_status_invalid"
    : safeErrorCode(rawFeaturePackEngineStatus.error, "feature_pack_engine_status_unavailable"),
};
const rawFeaturePackApi = serviceChecks.admin.ok && headers
  ? await fetchJson("http://127.0.0.1:9528/api/runtime-feature-packs", { headers, timeoutMs: 3_000 })
  : { ok: false, error: headers ? "admin_not_ready" : "internal_secret_missing" };
const featurePackSchemaValid = rawFeaturePackApi.ok
  && hasFeaturePackPayloadSchema(rawFeaturePackApi.payload);
const featurePackTruthMatchesEngine = featurePackSchemaValid
  && rawFeaturePackEngineStatus.ok
  && hasEngineFeaturePackStatusSchema(rawFeaturePackEngineStatus.payload)
  && featurePackApiMatchesEngine(rawFeaturePackApi.payload, rawFeaturePackEngineStatus.payload);
const featurePackApi = {
  ok: Boolean(featurePackTruthMatchesEngine),
  schemaValid: Boolean(featurePackSchemaValid),
  truthMatchesEngine: Boolean(featurePackTruthMatchesEngine),
  durationMs: rawFeaturePackApi.durationMs || null,
  error: !rawFeaturePackApi.ok
    ? safeErrorCode(rawFeaturePackApi.error, "feature_pack_api_unavailable")
    : !featurePackSchemaValid
      ? "feature_pack_schema_invalid"
      : !featurePackTruthMatchesEngine
        ? "feature_pack_truth_mismatch"
        : null,
};
const featurePackRuntime = !featurePackSmokeEnabled
  ? { ok: true, skipped: true, error: "feature_pack_probe_skipped" }
  : featurePackEngineStatus.ok
    ? await runFeaturePackRuntimeProbe(resourceRoot, rawFeaturePackEngineStatus.payload, featurePackProbeTimeoutMs)
    : { ok: false, error: "feature_pack_engine_status_unavailable" };
const checks = {
  ...serviceChecks,
  bootstrapSurface,
  ownerBootstrap,
  shellRestart,
  instanceManifest,
  shellProcess: {
    ok: isPidAlive(child.pid || 0),
    pid: child.pid || null,
  },
  shellSurface,
  desktopPet,
  startupBudget: {
    ok: Object.values(serviceChecks).every((item) => item.ok)
      && shellSurface.ok
      && startupDurationMs <= startupBudgetMs,
    durationMs: startupDurationMs,
    budgetMs: startupBudgetMs,
  },
  featurePackEngineStatus,
  featurePackRuntime,
  featurePackApi,
};

const payload = {
  startedAt,
  finishedAt: new Date().toISOString(),
  startupDurationMs,
  startupBudgetMs,
  serviceTimeoutMs,
  shellPid: child.pid || null,
  ports: {
    engine: 9530,
    admin: 9528,
    web: 9527,
  },
  checks,
  failureStage: firstFailureStage(checks),
  passed: Object.values(checks).every((item) => item.ok),
  note: "Automated packaged startup, product-surface, desktop-pet process/health, and authenticated Shell-control proof. Tray interaction and physical display behavior still require a matching host.",
};

const output = reportPath();
fs.writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(output);
console.log(JSON.stringify(payload, null, 2));
process.exit(payload.passed ? 0 : 1);
