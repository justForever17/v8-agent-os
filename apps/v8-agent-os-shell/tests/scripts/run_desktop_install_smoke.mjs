#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import net from "node:net";
import path from "node:path";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import readinessProbe from "../../lib/readiness-probe.cjs";
import desktopPetPlatform from "../../../v8-agent-os-cli/src/desktop_pet_platform.cjs";

const { validateReadinessResponse } = readinessProbe;
const {
  desktopPetAvailability,
  LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
} = desktopPetPlatform;

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

function occupyLoopbackPort(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer((socket) => socket.destroy());
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port, exclusive: true }, () => {
      server.removeListener("error", reject);
      resolve(server);
    });
  });
}

function closeServer(server) {
  if (!server?.listening) return Promise.resolve();
  return new Promise((resolve) => server.close(() => resolve()));
}

function loopbackPortOpen(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    const finish = (open) => {
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(300, () => finish(false));
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
  });
}

async function waitForManagedCleanup(ports, pids, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  let openPorts = [];
  let livePids = [];
  do {
    openPorts = [];
    for (const port of ports) {
      if (await loopbackPortOpen(port)) openPorts.push(port);
    }
    livePids = pids.filter((pid) => isPidAlive(pid));
    if (!openPorts.length && !livePids.length) break;
    await sleep(250);
  } while (Date.now() < deadline);
  return {
    ok: openPorts.length === 0 && livePids.length === 0,
    openPorts,
    livePids,
  };
}

async function waitForRuntimePorts(profilePath, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const profile = JSON.parse(fs.readFileSync(profilePath, "utf8"));
      const ports = profile?.ports;
      if (
        profile?.version === 1
        && profile?.policy === "web-fallback-v1"
        && ports?.engine === 9530
        && ports?.admin === 9528
        && Number.isInteger(ports?.web)
        && ports.web > 0
        && ports.web <= 65_535
      ) return profile;
    } catch {}
    await sleep(100);
  }
  return null;
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

function processCommandLine(pid) {
  if (process.platform !== "linux" || !Number.isInteger(pid) || pid <= 0) return "";
  try {
    return fs.readFileSync(`/proc/${pid}/cmdline`).toString("utf8").replaceAll("\0", " ").trim();
  } catch {
    return "";
  }
}

function packagedResourceRoot(shellExecutable) {
  const executableDir = path.dirname(path.resolve(shellExecutable));
  return process.platform === "darwin"
    ? path.resolve(executableDir, "..", "Resources", "v8os")
    : path.join(executableDir, "resources", "v8os");
}

function appImageRuntimeEnvironment(appImageRoot, noSandbox) {
  if (!appImageRoot) return {};
  const appDir = path.resolve(appImageRoot);
  const joinEnvironmentPaths = (...entries) => entries
    .filter((entry) => typeof entry === "string" && entry.length > 0)
    .join(path.delimiter);
  return {
    APPDIR: appDir,
    APPIMAGE: path.join(appDir, "AppRun"),
    PATH: joinEnvironmentPaths(appDir, path.join(appDir, "usr", "sbin"), process.env.PATH),
    XDG_DATA_DIRS: joinEnvironmentPaths(
      path.join(appDir, "usr", "share"),
      process.env.XDG_DATA_DIRS,
      "/usr/share/gnome",
      "/usr/local/share",
      "/usr/share",
    ),
    LD_LIBRARY_PATH: joinEnvironmentPaths(path.join(appDir, "usr", "lib"), process.env.LD_LIBRARY_PATH),
    GSETTINGS_SCHEMA_DIR: joinEnvironmentPaths(
      path.join(appDir, "usr", "share", "glib-2.0", "schemas"),
      process.env.GSETTINGS_SCHEMA_DIR,
    ),
    V8OS_ELECTRON_NO_SANDBOX: noSandbox ? "1" : "0",
  };
}

function spawnPackagedShell(
  shellExecutable,
  governedStateRoot,
  runtimeEnvironment = {},
  shellArgs = [],
) {
  const child = spawn(shellExecutable, shellArgs, {
    detached: true,
    env: {
      ...process.env,
      ...runtimeEnvironment,
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

function createSmokeOwnerCredentials() {
  const nonce = randomUUID().replaceAll("-", "");
  return {
    login: `v8os-smoke-${nonce.slice(0, 12)}`,
    name: "V8OS Smoke Owner",
    password: `${randomUUID()}${randomUUID()}`,
  };
}

async function bootstrapSmokeOwner(credentials) {
  const result = await fetchJson("http://127.0.0.1:9528/api/auth/bootstrap", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(credentials),
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

async function loginSmokeOwner(credentials) {
  const result = await fetchJson("http://127.0.0.1:9528/api/client/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      login: credentials.login,
      password: credentials.password,
    }),
    timeoutMs: 15_000,
  });
  const httpOk = result.ok && result.status === 200;
  const sessionIssued = typeof result.payload?.accessToken === "string"
    && result.payload.accessToken.trim().length > 0;
  const ownerIdentityMatched = result.payload?.user?.login === credentials.login
    && result.payload?.user?.role === "ADMIN";
  const ok = Boolean(httpOk && sessionIssued && ownerIdentityMatched);
  return {
    ok,
    httpOk,
    sessionIssued,
    ownerIdentityMatched,
    error: ok
      ? ""
      : !httpOk
        ? Number.isInteger(result.status)
          ? `owner_login_http_${result.status}`
          : safeErrorCode(result.error, "owner_login_request_failed")
        : !sessionIssued
          ? "owner_login_session_missing"
          : "owner_login_identity_mismatch",
  };
}

async function runPackagedCli(
  shellExecutable,
  resourceRoot,
  args,
  runtimeEnvironment = {},
  timeoutMs = 20_000,
) {
  const cliPath = path.join(resourceRoot, "apps", "v8-agent-os-cli", "bin", "v8os.mjs");
  if (!fs.existsSync(cliPath)) {
    return { ok: false, error: "packaged_cli_missing" };
  }
  const child = spawn(shellExecutable, [cliPath, ...args], {
    cwd: resourceRoot,
    env: {
      ...process.env,
      ...runtimeEnvironment,
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
      softwareRendering: descriptor?.softwareRendering === true,
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

function packagedCliItem(result, componentId) {
  try {
    const payload = JSON.parse(String(result?.stdout || ""));
    if (!Array.isArray(payload)) return null;
    return payload.find((item) => item?.id === componentId) || null;
  } catch {
    return null;
  }
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
  const documents = summarize(payload.documents, [
    "available",
    "isolated",
    "moduleOriginsVerified",
    "parsersVerified",
    "nativeToolVerified",
  ]);
  if (!rpa || !image || !documents || typeof payload.ok !== "boolean") {
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
  const documentsOk = documents.checked && !documents.error && (
    (documents.state === "not_installed" && documents.failClosed)
    || (documents.state === "installed" && !documents.failClosed
      && documents.available && documents.isolated
      && documents.moduleOriginsVerified && documents.parsersVerified && documents.nativeToolVerified)
  );
  const derivedOk = Boolean(rpaOk && imageOk && documentsOk);
  if (payload.ok !== derivedOk) {
    return { ok: false, error: "feature_pack_probe_invalid_response" };
  }
  return {
    ok: derivedOk,
    mode: "offline_runtime_probe",
    rpa,
    image,
    documents,
    error: payload.error ? safeErrorCode(payload.error, "feature_pack_probe_invalid_response") : null,
  };
}

async function runFeaturePackRuntimeProbe(resourceRoot, engineStatus, timeoutMs) {
  const execution = await runPackagedPythonProbe(
    resourceRoot,
    "feature_pack_runtime_probe.py",
    { engineStatus },
    timeoutMs,
    "feature_pack_probe",
  );
  if (!execution.ok) return execution;
  const summary = runtimeProbeSummary(execution.payload);
  return {
    ...summary,
    ok: execution.exitCode === 0 && summary.ok,
    durationMs: execution.durationMs,
    error: execution.exitCode === 2
      ? "feature_pack_probe_input_invalid"
      : execution.exitCode !== 0 && !summary.error
        ? "feature_pack_runtime_unhealthy"
        : summary.error,
  };
}

async function runPackagedPythonProbe(
  resourceRoot,
  probeFile,
  inputPayload,
  timeoutMs,
  errorPrefix,
) {
  const python = packagedPython(resourceRoot);
  const probePath = path.join(
    resourceRoot,
    "apps",
    "v8-agent-os-shell",
    "scripts",
    probeFile,
  );
  if (!python) return { ok: false, error: "packaged_python_missing" };
  if (!fs.existsSync(probePath)) return { ok: false, error: `${errorPrefix}_missing` };

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
  child.stdin.end(`${JSON.stringify(inputPayload || {})}\n`);
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    stopProbe();
  }, timeoutMs);
  const result = await resultPromise;
  clearTimeout(timeout);
  if (terminationFallback) clearTimeout(terminationFallback);
  const durationMs = Date.now() - startedAt;
  if (timedOut) return { ok: false, durationMs, error: `${errorPrefix}_timeout` };
  if (outputOverflow) return { ok: false, durationMs, error: `${errorPrefix}_output_limit` };
  if (result.error) return { ok: false, durationMs, error: `${errorPrefix}_spawn_failed` };
  let parsed;
  try {
    const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length !== 1) throw new Error("invalid probe output");
    parsed = JSON.parse(lines[0]);
  } catch {
    return { ok: false, durationMs, error: `${errorPrefix}_invalid_response` };
  }
  return {
    ok: true,
    payload: parsed,
    exitCode: result.code,
    durationMs,
  };
}

function commandRuntimeProbeSummary(payload) {
  if (!payload || typeof payload !== "object" || payload.mode !== "packaged_command_runtime_probe") {
    return { ok: false, error: "command_runtime_probe_invalid_response" };
  }
  const ordinary = payload.ordinary;
  const failure = payload.failure;
  const timeout = payload.timeout;
  const interactive = payload.interactive;
  const interactiveExit = payload.interactiveExit;
  if (![ordinary, failure, timeout, interactive, interactiveExit].every((item) => item && typeof item === "object")) {
    return { ok: false, error: "command_runtime_probe_invalid_response" };
  }
  const derivedOk = Boolean(
    ordinary.backend === "pipe"
    && ordinary.completed === true
    && ordinary.exitCodeObserved === true
    && ordinary.outputObserved === true
    && failure.backend === "pipe"
    && failure.completed === true
    && failure.exitCodeObserved === true
    && failure.failureClassified === true
    && timeout.backend === "pipe"
    && timeout.completed === true
    && timeout.timedOut === true
    && timeout.deadlineClassified === true
    && timeout.processTreeStopped === true
    && ["winpty", "posix_pty"].includes(interactive.backend)
    && interactive.backendExpected === true
    && interactive.usesTty === true
    && interactive.roundTrip === true
    && interactive.processTreeStopped === true
    && interactiveExit.backend === interactive.backend
    && interactiveExit.backendExpected === true
    && interactiveExit.completed === true
    && interactiveExit.exitCodeObserved === true
    && interactiveExit.failureClassified === true
    && interactiveExit.timedOut === false
  );
  if (typeof payload.ok !== "boolean" || payload.ok !== derivedOk) {
    return { ok: false, error: "command_runtime_probe_invalid_response" };
  }
  return {
    ok: derivedOk,
    mode: "packaged_command_runtime_probe",
    ordinary: {
      backend: ordinary.backend,
      completed: ordinary.completed,
      exitCodeObserved: ordinary.exitCodeObserved,
      outputObserved: ordinary.outputObserved,
    },
    failure: {
      backend: failure.backend,
      completed: failure.completed,
      exitCodeObserved: failure.exitCodeObserved,
      failureClassified: failure.failureClassified,
    },
    timeout: {
      backend: timeout.backend,
      completed: timeout.completed,
      timedOut: timeout.timedOut,
      deadlineClassified: timeout.deadlineClassified,
      processTreeStopped: timeout.processTreeStopped,
    },
    interactive: {
      backend: interactive.backend,
      backendExpected: interactive.backendExpected,
      usesTty: interactive.usesTty,
      roundTrip: interactive.roundTrip,
      processTreeStopped: interactive.processTreeStopped,
    },
    interactiveExit: {
      backend: interactiveExit.backend,
      backendExpected: interactiveExit.backendExpected,
      completed: interactiveExit.completed,
      exitCodeObserved: interactiveExit.exitCodeObserved,
      failureClassified: interactiveExit.failureClassified,
      timedOut: interactiveExit.timedOut,
    },
    error: payload.error ? safeErrorCode(payload.error, "command_runtime_unhealthy") : null,
  };
}

async function runCommandRuntimeProbe(resourceRoot, timeoutMs) {
  const execution = await runPackagedPythonProbe(
    resourceRoot,
    "command_runtime_probe.py",
    {},
    timeoutMs,
    "command_runtime_probe",
  );
  if (!execution.ok) return execution;
  const summary = commandRuntimeProbeSummary(execution.payload);
  return {
    ...summary,
    ok: execution.exitCode === 0 && summary.ok,
    durationMs: execution.durationMs,
    error: execution.exitCode !== 0 && !summary.error
      ? "command_runtime_unhealthy"
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
  const requiredIds = new Set(["rpa_automation", "creative_media_image_analysis", "document_ingestion"]);
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
    documents: summary("document_ingestion"),
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

  const requiredAdminPackIds = new Set(["rpa_automation", "creative_media_image_analysis", "document_ingestion"]);
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
  for (const id of ["rpa_automation", "creative_media_image_analysis", "document_ingestion"]) {
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

function featurePackById(payload, key, packId) {
  const packs = Array.isArray(payload?.[key]) ? payload[key] : [];
  return packs.find((pack) => String(pack?.id || "") === packId) || null;
}

async function installDocumentFeaturePack({ headers, ports, timeoutMs }) {
  const startedAt = Date.now();
  if (!headers) return { ok: false, error: "internal_secret_missing", durationMs: 0 };
  const installRequest = await fetchJson(`http://127.0.0.1:${ports.admin}/api/runtime-feature-packs`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ packId: "document_ingestion", locale: "zh-CN" }),
    timeoutMs: 15_000,
  });
  const requestStatus = String(installRequest.payload?.status || "");
  const sourceStrategy = Array.isArray(installRequest.payload?.sourceStrategy)
    ? installRequest.payload.sourceStrategy.map((source) => String(source?.id || "")).filter(Boolean)
    : [];
  if (!installRequest.ok || !["started", "installing"].includes(requestStatus)) {
    return {
      ok: false,
      requestStatus,
      sourceStrategy,
      durationMs: Date.now() - startedAt,
      error: installRequest.ok ? "document_pack_install_not_started" : "document_pack_install_request_failed",
    };
  }

  const deadline = startedAt + timeoutMs;
  let installed = false;
  let failed = false;
  let lastEnginePayload = null;
  while (Date.now() < deadline) {
    const [engineState, adminState] = await Promise.all([
      fetchJson(`http://127.0.0.1:${ports.engine}/v1/runtime-feature-packs/status`, {
        headers,
        timeoutMs: 3_000,
      }),
      fetchJson(`http://127.0.0.1:${ports.admin}/api/runtime-feature-packs?refresh=1`, {
        headers,
        timeoutMs: 3_000,
      }),
    ]);
    if (engineState.ok) lastEnginePayload = engineState.payload;
    const enginePack = featurePackById(engineState.payload, "featurePacks", "document_ingestion");
    const adminPack = featurePackById(adminState.payload, "packs", "document_ingestion");
    failed = enginePack?.status === "failed" || adminPack?.status === "failed";
    installed = Boolean(
      engineState.ok
      && adminState.ok
      && enginePack?.status === "installed"
      && enginePack?.installed === true
      && adminPack?.status === "installed"
      && adminPack?.installed === true
    );
    if (installed || failed) break;
    await sleep(1_000);
  }
  if (!installed) {
    return {
      ok: false,
      requestStatus,
      sourceStrategy,
      durationMs: Date.now() - startedAt,
      error: failed ? "document_pack_install_failed" : "document_pack_install_timeout",
    };
  }

  const restart = await runPackagedCli(
    shellExe,
    resourceRoot,
    ["restart", "--only", "engine", "--json"],
    runtimeEnvironment,
    60_000,
  );
  if (!restart.ok) {
    return {
      ok: false,
      requestStatus,
      sourceStrategy,
      installed: true,
      engineRestarted: false,
      durationMs: Date.now() - startedAt,
      error: "document_pack_engine_restart_failed",
    };
  }
  const engineReady = await waitForReadiness(
    "engine",
    `http://127.0.0.1:${ports.engine}/readyz`,
    Math.min(60_000, timeoutMs),
  );
  if (!engineReady.ok) {
    return {
      ok: false,
      requestStatus,
      sourceStrategy,
      installed: true,
      engineRestarted: true,
      durationMs: Date.now() - startedAt,
      error: "document_pack_engine_not_ready",
    };
  }

  const finalEngineState = await fetchJson(
    `http://127.0.0.1:${ports.engine}/v1/runtime-feature-packs/status`,
    { headers, timeoutMs: 3_000 },
  );
  const finalPack = featurePackById(finalEngineState.payload, "featurePacks", "document_ingestion");
  const finalStateValid = Boolean(
    finalEngineState.ok
    && finalPack?.status === "installed"
    && finalPack?.installed === true
    && finalPack?.restartRequired === false
  );
  return {
    ok: finalStateValid,
    requestStatus,
    sourceStrategy,
    installed: true,
    engineRestarted: true,
    restartRequired: finalPack?.restartRequired ?? null,
    durationMs: Date.now() - startedAt,
    error: finalStateValid ? null : "document_pack_restart_state_invalid",
    enginePayload: finalStateValid ? finalEngineState.payload : lastEnginePayload,
  };
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

const shellExeInput = argValue("--shell-exe") || process.env.V8OS_SHELL_EXE || "";
const shellExe = shellExeInput ? path.resolve(shellExeInput) : "";
const explicitResourceRoot = argValue("--resource-root");
const explicitAppImageRoot = argValue("--appimage-root");
const shellNoSandbox = booleanArg("--shell-no-sandbox", false);
const softwareRendering = booleanArg("--software-rendering", false);
const serviceTimeoutMs = positiveIntegerArg("--timeout-ms", 90_000);
const startupBudgetMs = positiveIntegerArg("--startup-budget-ms", 90_000);
const stabilityWindowMs = positiveIntegerArg("--stability-window-ms", 15_000);
const featurePackSmokeEnabled = booleanArg("--feature-pack-smoke", true);
const installDocumentPack = booleanArg("--install-document-pack", false);
const occupyDefaultWebPort = booleanArg("--occupy-default-web-port", false);
const featurePackProbeTimeoutMs = Math.min(120_000, positiveIntegerArg("--feature-pack-probe-timeout-ms", 30_000));
const commandProbeTimeoutMs = Math.min(120_000, positiveIntegerArg("--command-probe-timeout-ms", 45_000));
const documentPackInstallTimeoutMs = Math.min(
  15 * 60_000,
  positiveIntegerArg("--document-pack-install-timeout-ms", 8 * 60_000),
);
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
const runtimePortsPath = path.join(stateRoot, "runtime", "cli", "ports.json");
const resourceRoot = explicitResourceRoot
  ? path.resolve(explicitResourceRoot)
  : packagedResourceRoot(shellExe);
if (!fs.existsSync(resourceRoot) || !fs.statSync(resourceRoot).isDirectory()) {
  console.error(`Packaged resource root is not a directory: ${resourceRoot}`);
  process.exit(2);
}
const packagedEngineRoot = path.join(resourceRoot, "apps", "v8-agent-os-engine");
const packagedPythonPath = packagedPython(resourceRoot);
const packagedRuntimeLayout = {
  ok: Boolean(packagedPythonPath && !fs.existsSync(path.join(packagedEngineRoot, ".venv"))),
  portablePythonPresent: Boolean(packagedPythonPath),
  devVenvAbsent: !fs.existsSync(path.join(packagedEngineRoot, ".venv")),
  error: !packagedPythonPath
    ? "packaged_portable_python_missing"
    : fs.existsSync(path.join(packagedEngineRoot, ".venv"))
      ? "packaged_dev_venv_present"
      : "",
};
const appImageRoot = explicitAppImageRoot ? path.resolve(explicitAppImageRoot) : "";
if (appImageRoot) {
  const relativeShellPath = path.relative(appImageRoot, path.resolve(shellExe));
  if (
    process.platform !== "linux"
    || !fs.existsSync(appImageRoot)
    || !fs.statSync(appImageRoot).isDirectory()
    || !fs.existsSync(path.join(appImageRoot, "AppRun"))
    || relativeShellPath.startsWith("..")
    || path.isAbsolute(relativeShellPath)
  ) {
    console.error("Invalid --appimage-root for the packaged Shell executable.");
    process.exit(2);
  }
}
const runtimeEnvironment = {
  ...appImageRuntimeEnvironment(appImageRoot, shellNoSandbox),
  V8OS_DESKTOP_ISOLATED_USER_DATA_ROOT: path.join(stateRoot, "runtime", "electron-user-data"),
};
const shellArgs = [
  ...(shellNoSandbox ? ["--no-sandbox"] : []),
  ...(softwareRendering ? ["--v8os-software-rendering"] : []),
];
const defaultWebPortBlocker = occupyDefaultWebPort ? await occupyLoopbackPort(9527) : null;
let child = spawnPackagedShell(shellExe, stateRoot, runtimeEnvironment, shellArgs);
const runtimePortProfile = await waitForRuntimePorts(runtimePortsPath, Math.min(serviceTimeoutMs, 15_000));
const runtimePorts = runtimePortProfile?.ports || { engine: 9530, admin: 9528, web: 9527 };

const [engine, admin, web, initialShellSurface] = await Promise.all([
  waitForReadiness("engine", `http://127.0.0.1:${runtimePorts.engine}/readyz`, serviceTimeoutMs),
  waitForReadiness("admin", `http://127.0.0.1:${runtimePorts.admin}/login`, serviceTimeoutMs),
  waitForReadiness("web", `http://127.0.0.1:${runtimePorts.web}/chat`, serviceTimeoutMs),
  waitForShellSurface(shellControlPath, child.pid || 0, serviceTimeoutMs),
]);
const startupDurationMs = Date.now() - startedAtMs;
await sleep(stabilityWindowMs);
const [stableEngine, stableAdmin, stableWeb, stableShellSurface] = await Promise.all([
  waitForReadiness("engine", `http://127.0.0.1:${runtimePorts.engine}/readyz`, 3_000),
  waitForReadiness("admin", `http://127.0.0.1:${runtimePorts.admin}/login`, 3_000),
  waitForReadiness("web", `http://127.0.0.1:${runtimePorts.web}/chat`, 3_000),
  waitForShellSurface(shellControlPath, child.pid || 0, 3_000),
]);
const stabilityCompletedDurationMs = Date.now() - startedAtMs;
const initialRuntimeStability = {
  ok: Boolean(
    isPidAlive(child.pid || 0)
    && stableEngine.ok
    && stableAdmin.ok
    && stableWeb.ok
    && stableShellSurface.ok
  ),
  windowMs: stabilityWindowMs,
  completedDurationMs: stabilityCompletedDurationMs,
  shellAlive: isPidAlive(child.pid || 0),
  services: {
    engine: stableEngine.ok,
    admin: stableAdmin.ok,
    web: stableWeb.ok,
    shellSurface: stableShellSurface.ok,
  },
  error: "",
};
if (!initialRuntimeStability.ok) initialRuntimeStability.error = "runtime_unstable_after_initial_readiness";
const shellCommandLine = processCommandLine(child.pid || 0);
const sandboxMode = {
  ok: process.platform !== "linux" || Boolean(shellCommandLine && !shellCommandLine.split(/\s+/).includes("--no-sandbox")),
  platform: process.platform,
  commandLine: shellCommandLine || null,
  error: process.platform === "linux" && !shellCommandLine
    ? "shell_command_line_unavailable"
    : process.platform === "linux" && shellCommandLine.split(/\s+/).includes("--no-sandbox")
      ? "no_sandbox_flag_observed"
      : "",
};
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

const ownerCredentials = createSmokeOwnerCredentials();
let ownerBootstrap = { ok: false, error: "initial_bootstrap_surface_unavailable" };
let existingOwnerLogin = { ok: false, error: "shell_restart_unavailable" };
let shellRestart = { ok: false, error: "owner_bootstrap_unavailable" };
let shellSurface = initialShellSurface;
let rawInstanceManifest = rawInitialInstanceManifest;
if (bootstrapSurface.ok) {
  ownerBootstrap = await bootstrapSmokeOwner(ownerCredentials);
  if (ownerBootstrap.ok) {
    const initialShellPid = child.pid || 0;
    const stopped = await runPackagedCli(
      shellExe,
      resourceRoot,
      ["stop", "--only", "shell", "--json"],
      runtimeEnvironment,
      45_000,
    );
    const exited = stopped.ok && await waitForPidExit(initialShellPid);
    shellRestart = {
      ok: Boolean(stopped.ok && exited),
      stopped: Boolean(stopped.ok),
      oldProcessExited: Boolean(exited),
      cliExitCode: stopped.exitCode,
      cliSignal: stopped.signal,
      cliError: stopped.error,
      cliStderr: String(stopped.stderr || '').slice(-4000),
      error: !stopped.ok ? "initial_shell_stop_failed" : exited ? "" : "initial_shell_exit_timeout",
    };
    if (shellRestart.ok) {
      child = spawnPackagedShell(shellExe, stateRoot, runtimeEnvironment, shellArgs);
      shellSurface = await waitForShellSurface(shellControlPath, child.pid || 0, serviceTimeoutMs);
      rawInstanceManifest = await fetchJson("http://127.0.0.1:9528/api/client/instance", { timeoutMs: 3_000 });
      existingOwnerLogin = await loginSmokeOwner(ownerCredentials);
    }
  }
}
const instanceManifestValid = rawInstanceManifest.ok
  && rawInstanceManifest.payload?.kind === "v8_instance_manifest"
  && rawInstanceManifest.payload?.initialized === true;
shellSurface.ok = Boolean(
  shellSurface.ok
  && instanceManifestValid
  && shellSurface.surfaceKind === "admin-login",
);
shellSurface.expectedSurfaceKind = "admin-login";
shellSurface.error = shellSurface.ok
  ? ""
  : instanceManifestValid
    ? "admin_auth_lock_surface_mismatch"
    : "initialized_instance_manifest_invalid";
const instanceManifest = {
  ok: Boolean(instanceManifestValid),
  initialized: instanceManifestValid ? true : null,
  error: instanceManifestValid ? "" : safeErrorCode(rawInstanceManifest.error, "instance_manifest_invalid"),
};
const serviceChecks = { engine, admin, web };
const desktopPetStartedAtMs = Date.now();
const coreSurfaceReady = Object.values(serviceChecks).every((item) => item.ok) && shellSurface.ok;
const expectedDesktopPetAvailability = desktopPetAvailability();
const desktopPetLaunch = coreSurfaceReady
  ? await runPackagedCli(
    shellExe,
    resourceRoot,
    ["start", "--only", "desktop-pet", "--mode", "start", "--json"],
    runtimeEnvironment,
  )
  : { ok: false, error: "core_surface_not_ready" };
let desktopPet;
if (!expectedDesktopPetAvailability.available) {
  const desktopPetStatus = coreSurfaceReady
    ? await runPackagedCli(
      shellExe,
      resourceRoot,
      ["status", "--json"],
      runtimeEnvironment,
    )
    : { ok: false, error: "core_surface_not_ready" };
  const launchItem = packagedCliItem(desktopPetLaunch, "desktop-pet");
  const statusItem = packagedCliItem(desktopPetStatus, "desktop-pet");
  const launchRejected = desktopPetLaunch.ok === false
    && Number.isInteger(desktopPetLaunch.exitCode)
    && desktopPetLaunch.exitCode !== 0;
  const launchUnavailable = launchItem?.componentId === "desktop-pet"
    && launchItem?.available === false
    && launchItem?.status === "unavailable"
    && launchItem?.reasonCode === LINUX_DESKTOP_PET_UNAVAILABLE_REASON
    && launchItem?.pid == null;
  const statusUnavailable = desktopPetStatus.ok
    && statusItem?.componentId === "desktop-pet"
    && statusItem?.available === false
    && statusItem?.status === "unavailable"
    && statusItem?.reasonCode === LINUX_DESKTOP_PET_UNAVAILABLE_REASON;
  const processRunning = statusItem?.pidAlive === true;
  const processAbsent = statusItem?.pid == null && statusItem?.pidAlive === false;
  const descriptorCreated = fs.existsSync(desktopPetDescriptorPath);
  const ok = Boolean(
    coreSurfaceReady
    && launchRejected
    && launchUnavailable
    && statusUnavailable
    && processAbsent
    && !descriptorCreated
  );
  desktopPet = {
    ok,
    mode: "unavailable",
    available: false,
    reasonCode: LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
    launchRejected,
    launchUnavailable: Boolean(launchUnavailable),
    statusUnavailable: Boolean(statusUnavailable),
    processRunning,
    descriptorCreated,
    launchExitCode: desktopPetLaunch.exitCode ?? null,
    error: ok
      ? ""
      : !coreSurfaceReady
        ? "core_surface_not_ready"
        : !launchRejected
          ? "desktop_pet_linux_start_not_rejected"
          : !launchUnavailable
            ? "desktop_pet_linux_start_contract_invalid"
            : !statusUnavailable
              ? "desktop_pet_linux_status_contract_invalid"
              : !processAbsent
                ? "desktop_pet_linux_process_detected"
                : "desktop_pet_linux_descriptor_created",
  };
} else {
  desktopPet = desktopPetLaunch.ok
    ? await waitForDesktopPet(desktopPetDescriptorPath, shellControlPath, Math.min(serviceTimeoutMs, 30_000))
    : { ok: false, error: safeErrorCode(desktopPetLaunch.error, "desktop_pet_launch_failed") };
  desktopPet.mode = "running";
  desktopPet.available = true;
  desktopPet.reasonCode = null;
}
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
const rawDocumentPackInstall = installDocumentPack
  ? await installDocumentFeaturePack({
    headers,
    ports: runtimePorts,
    timeoutMs: documentPackInstallTimeoutMs,
  })
  : { ok: true, skipped: true, error: "document_pack_install_skipped" };
const { enginePayload: installedDocumentPackEnginePayload, ...documentPackInstall } = rawDocumentPackInstall;
const effectiveFeaturePackEnginePayload = installDocumentPack
  ? installedDocumentPackEnginePayload
  : rawFeaturePackEngineStatus.payload;
const featurePackRuntime = !featurePackSmokeEnabled
  ? { ok: true, skipped: true, error: "feature_pack_probe_skipped" }
  : (installDocumentPack ? documentPackInstall.ok : featurePackEngineStatus.ok)
    ? await runFeaturePackRuntimeProbe(resourceRoot, effectiveFeaturePackEnginePayload, featurePackProbeTimeoutMs)
    : { ok: false, error: "feature_pack_engine_status_unavailable" };
const commandRuntime = packagedRuntimeLayout.ok
  ? await runCommandRuntimeProbe(resourceRoot, commandProbeTimeoutMs)
  : { ok: false, error: "packaged_runtime_layout_unavailable" };
const shellProcessBeforeCleanup = isPidAlive(child.pid || 0);
const managedPids = [child.pid, desktopPet.pid, desktopPet.serverPid]
  .map(Number)
  .filter((pid) => Number.isInteger(pid) && pid > 0);
const stopAll = await runPackagedCli(
  shellExe,
  resourceRoot,
  ["stop", "--all", "--json"],
  runtimeEnvironment,
  45_000,
);
const cleanupProof = await waitForManagedCleanup(
  [runtimePorts.engine, runtimePorts.admin, runtimePorts.web],
  managedPids,
  25_000,
);
const defaultWebPortStillOccupied = occupyDefaultWebPort
  ? await loopbackPortOpen(9527)
  : true;
const packagedCleanup = {
  ok: Boolean(stopAll.ok && cleanupProof.ok && defaultWebPortStillOccupied),
  cliStopped: Boolean(stopAll.ok),
  cliExitCode: stopAll.exitCode,
  cliSignal: stopAll.signal,
  cliError: stopAll.error,
  cliStderr: String(stopAll.stderr || '').slice(-4000),
  managedPortsClosed: cleanupProof.openPorts.length === 0,
  managedProcessesExited: cleanupProof.livePids.length === 0,
  externalDefaultPortPreserved: defaultWebPortStillOccupied,
  error: !stopAll.ok
    ? "packaged_stop_all_failed"
    : !cleanupProof.ok
      ? "managed_runtime_cleanup_incomplete"
      : !defaultWebPortStillOccupied
        ? "external_default_port_was_not_preserved"
        : "",
};
const checks = {
  ...serviceChecks,
  packagedRuntimeLayout,
  initialRuntimeStability,
  sandboxMode,
  bootstrapSurface,
  ownerBootstrap,
  shellRestart,
  existingOwnerLogin,
  instanceManifest,
  shellProcess: {
    ok: shellProcessBeforeCleanup,
    pid: child.pid || null,
  },
  shellSurface,
  renderingMode: {
    ok: shellSurface.softwareRendering === softwareRendering,
    requested: softwareRendering,
    observed: shellSurface.softwareRendering === true,
    error: shellSurface.softwareRendering === softwareRendering ? "" : "software_rendering_mode_mismatch",
  },
  adaptiveWebPort: {
    ok: Boolean(runtimePortProfile
      && (!occupyDefaultWebPort
        || (runtimePorts.web !== 9527 && defaultWebPortBlocker?.listening === true))),
    defaultPortOccupied: occupyDefaultWebPort,
    selectedPort: runtimePorts.web,
    fallbackSelected: runtimePorts.web !== 9527,
    error: runtimePortProfile
      ? occupyDefaultWebPort && runtimePorts.web === 9527
        ? "occupied_default_web_port_not_avoided"
        : ""
      : "runtime_port_profile_missing",
  },
  desktopPet,
  startupBudget: {
    ok: Object.values(serviceChecks).every((item) => item.ok)
      && initialShellSurface.ok
      && startupDurationMs <= startupBudgetMs,
    durationMs: startupDurationMs,
    budgetMs: startupBudgetMs,
  },
  featurePackEngineStatus,
  featurePackRuntime,
  featurePackApi,
  documentPackInstall,
  commandRuntime,
  packagedCleanup,
};

const payload = {
  startedAt,
  finishedAt: new Date().toISOString(),
  startupDurationMs,
  startupBudgetMs,
  serviceTimeoutMs,
  stabilityWindowMs,
  shellPid: child.pid || null,
  ports: {
    ...runtimePorts,
  },
  checks,
  failureStage: firstFailureStage(checks),
  passed: Object.values(checks).every((item) => item.ok),
  note: "Automated packaged startup, product-surface, existing-Owner login, fixed ordinary/interactive command backend probes, Windows/macOS desktop-pet process/health, or governed Linux desktop-pet unavailability/no-process proof. Tray interaction and physical display behavior still require a matching host.",
};

const output = reportPath();
fs.writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(output);
console.log(JSON.stringify(payload, null, 2));
await closeServer(defaultWebPortBlocker);
process.exit(payload.passed ? 0 : 1);
