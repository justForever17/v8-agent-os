import fs from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { ensureDir } from "./json_file.mjs";
import { COMPONENTS, logPathsFor } from "./components.mjs";
import { LOG_DIR, STATE_ROOT } from "./paths.mjs";
import { isPortOpen } from "./ports.mjs";
import { isPidAlive, readProcessState, writeProcessState } from "./process_state.mjs";

function componentHasPort(component) {
  return Number.isInteger(component?.port) && component.port > 0;
}

function normalizeProcessText(value) {
  return String(value || "").replaceAll("/", "\\").toLowerCase();
}

function processCandidateMatchesEngine(candidate, commandSpec) {
  if (!candidate || typeof candidate !== "object") return false;
  const executable = normalizeProcessText(candidate.executablePath);
  const commandLine = normalizeProcessText(candidate.commandLine);
  const canonicalExecutable = normalizeProcessText(path.resolve(commandSpec.command));
  const canonicalEngineDir = normalizeProcessText(path.resolve(commandSpec.cwd));
  const engineSignature = /(?:^|\s)(?:-m\s+uvicorn\s+main:app|[^\s]*main\.py)(?:\s|$)/i.test(commandLine);
  const ownedRuntime = executable === canonicalExecutable
    || executable.startsWith(`${canonicalEngineDir}\\`)
    || commandLine.includes(canonicalEngineDir);
  return engineSignature && ownedRuntime;
}

export function verifiedComponentPortOwner(componentId, descriptor) {
  if (componentId !== "engine" || !descriptor || typeof descriptor !== "object") return null;
  const component = COMPONENTS[componentId];
  if (!component) return null;
  const commandSpec = component.command({ mode: "start" });
  const owner = {
    pid: Number(descriptor.pid),
    executablePath: descriptor.executablePath,
    commandLine: descriptor.commandLine,
  };
  const parent = {
    pid: Number(descriptor.parentPid),
    executablePath: descriptor.parentExecutablePath,
    commandLine: descriptor.parentCommandLine,
  };
  const ownerMatches = Number.isInteger(owner.pid) && owner.pid > 0 && processCandidateMatchesEngine(owner, commandSpec);
  const parentMatches = Number.isInteger(parent.pid) && parent.pid > 0 && processCandidateMatchesEngine(parent, commandSpec);
  if (!ownerMatches && !parentMatches) return null;
  return {
    ownerPid: owner.pid,
    killPid: parentMatches ? parent.pid : owner.pid,
    matchedBy: parentMatches ? "verified_parent_runtime" : "verified_port_owner",
  };
}

function readWindowsListeningProcessDescriptor(port) {
  if (process.platform !== "win32" || !Number.isInteger(Number(port))) return null;
  const script = [
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()",
    `$connection = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1`,
    "if (-not $connection) { exit 0 }",
    "$process = Get-CimInstance Win32_Process -Filter \"ProcessId = $($connection.OwningProcess)\" -ErrorAction SilentlyContinue",
    "if (-not $process) { exit 0 }",
    "$parent = if ($process.ParentProcessId) { Get-CimInstance Win32_Process -Filter \"ProcessId = $($process.ParentProcessId)\" -ErrorAction SilentlyContinue } else { $null }",
    "[pscustomobject]@{ pid = [int]$process.ProcessId; parentPid = [int]$process.ParentProcessId; executablePath = [string]$process.ExecutablePath; commandLine = [string]$process.CommandLine; parentExecutablePath = [string]$parent.ExecutablePath; parentCommandLine = [string]$parent.CommandLine } | ConvertTo-Json -Compress",
  ].join("; ");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.status !== 0 || !String(result.stdout || "").trim()) return null;
  try {
    return JSON.parse(String(result.stdout).trim());
  } catch {
    return null;
  }
}

const DESKTOP_PET_PROCESS_PATH = path.join(STATE_ROOT, "runtime", "desktop-pet.json");
const SHELL_CONTROL_PATH = path.join(STATE_ROOT, "runtime", "shell-control.json");

function readDesktopPetProcessDescriptor() {
  try {
    const descriptor = JSON.parse(fs.readFileSync(DESKTOP_PET_PROCESS_PATH, "utf8"));
    const pid = Number(descriptor?.pid);
    if (!Number.isInteger(pid) || pid <= 0) return null;
    return { ...descriptor, pid };
  } catch {
    return null;
  }
}

function readShellControlDescriptor() {
  try {
    const descriptor = JSON.parse(fs.readFileSync(SHELL_CONTROL_PATH, "utf8"));
    const pid = Number(descriptor?.pid);
    if (!Number.isInteger(pid) || pid <= 0) return null;
    return { ...descriptor, pid };
  } catch {
    return null;
  }
}

function effectiveManagedPid(componentId, record) {
  if (componentId === "desktop-pet") {
    const descriptor = readDesktopPetProcessDescriptor();
    if (descriptor?.pid && isPidAlive(descriptor.pid)) return descriptor.pid;
  }
  return record?.pid || null;
}

export async function statusComponents(componentIds = Object.keys(COMPONENTS)) {
  const state = readProcessState();
  const statuses = [];
  for (const id of componentIds) {
    const component = COMPONENTS[id];
    if (!component) continue;
    const record = state.processes[id] || null;
    const effectivePid = effectiveManagedPid(id, record);
    const pidAlive = effectivePid ? isPidAlive(effectivePid) : false;
    const hasPort = componentHasPort(component);
    const portOpen = hasPort ? await isPortOpen(component.port) : false;
    statuses.push({
      id,
      label: component.label,
      port: component.port,
      managed: Boolean((record && record.managed) || (id === "desktop-pet" && effectivePid)),
      pid: effectivePid,
      pidAlive,
      portOpen,
      state: pidAlive ? "managed_running" : hasPort && portOpen ? "external_port_in_use" : "stopped",
      startedAt: id === "desktop-pet"
        ? (readDesktopPetProcessDescriptor()?.startedAt || record?.startedAt || null)
        : (record?.startedAt || null),
      logOut: record?.logOut || null,
      logErr: record?.logErr || null,
    });
  }
  return statuses;
}

export async function startComponents(componentIds, options = {}) {
  ensureDir(LOG_DIR);
  const state = readProcessState();
  const results = [];
  for (const id of componentIds) {
    const component = COMPONENTS[id];
    if (!component) continue;
    const record = state.processes[id] || null;
    const effectivePid = effectiveManagedPid(id, record);
    if (effectivePid && isPidAlive(effectivePid)) {
      results.push({ id, status: "already_running", pid: effectivePid });
      continue;
    }
    if (componentHasPort(component) && await isPortOpen(component.port)) {
      results.push({ id, status: "port_in_use", port: component.port });
      continue;
    }
    const commandSpec = component.command(options);
    if (!fs.existsSync(commandSpec.cwd)) {
      results.push({ id, status: "missing_cwd", cwd: commandSpec.cwd });
      continue;
    }
    const logs = logPathsFor(id);
    const out = fs.openSync(logs.out, "a");
    const err = fs.openSync(logs.err, "a");
    const child = spawn(commandSpec.command, commandSpec.args, {
      cwd: commandSpec.cwd,
      env: { ...process.env, ...commandSpec.env },
      detached: true,
      stdio: ["ignore", out, err],
      windowsHide: true,
    });
    child.unref();
    state.processes[id] = {
      managed: true,
      pid: child.pid,
      command: commandSpec.command,
      args: commandSpec.args,
      cwd: commandSpec.cwd,
      port: componentHasPort(component) ? component.port : null,
      startedAt: new Date().toISOString(),
      logOut: logs.out,
      logErr: logs.err,
    };
    results.push({ id, status: "started", pid: child.pid, port: componentHasPort(component) ? component.port : null, logOut: logs.out, logErr: logs.err });
  }
  writeProcessState(state);
  return results;
}

function killPid(pid, options = {}) {
  const numeric = Number(pid);
  if (!Number.isInteger(numeric) || numeric <= 0) return { ok: false, reason: "invalid_pid" };
  if (!isPidAlive(numeric)) return { ok: true, reason: "already_stopped" };
  if (process.platform === "win32") {
    const args = ["/PID", String(numeric)];
    if (options.tree !== false) args.push("/T");
    args.push("/F");
    const result = spawnSync("taskkill", args, { encoding: "utf8", windowsHide: true });
    if (result.status !== 0 && !isPidAlive(numeric)) {
      return { ok: true, reason: "stopped_during_kill" };
    }
    return { ok: result.status === 0, reason: result.status === 0 ? "killed" : (result.stderr || result.stdout || "taskkill_failed").trim() };
  }
  try {
    process.kill(-numeric, "SIGTERM");
  } catch {
    try {
      process.kill(numeric, "SIGTERM");
    } catch (error) {
      return { ok: false, reason: error instanceof Error ? error.message : "kill_failed" };
    }
  }
  return { ok: true, reason: "killed" };
}

export function stopComponents(componentIds = Object.keys(COMPONENTS), options = {}) {
  const state = readProcessState();
  const results = [];
  for (const id of componentIds) {
    const component = COMPONENTS[id];
    if (!component) continue;
    const record = state.processes[id];
    const desktopPetDescriptor = id === "desktop-pet" ? readDesktopPetProcessDescriptor() : null;
    const shellControlDescriptor = id === "shell" ? readShellControlDescriptor() : null;
    const recordedPidAlive = record?.pid ? isPidAlive(record.pid) : false;
    const mayStopVerifiedPortOwner = Array.isArray(options.stopVerifiedPortOwners)
      && options.stopVerifiedPortOwners.includes(id)
      && componentHasPort(component)
      && !recordedPidAlive;
    const verifiedPortOwner = mayStopVerifiedPortOwner
      ? verifiedComponentPortOwner(id, readWindowsListeningProcessDescriptor(component.port))
      : null;
    if (!record && !desktopPetDescriptor && !shellControlDescriptor && !verifiedPortOwner) {
      results.push({ id, status: "not_managed" });
      continue;
    }
    const targetPids = [...new Set([
      desktopPetDescriptor?.pid,
      shellControlDescriptor?.pid,
      record?.pid,
      verifiedPortOwner?.killPid,
    ].filter(Boolean))];
    // Windows keeps the original parent process relationship even for detached
    // children. Killing the whole Shell tree can therefore terminate the managed
    // desktop pet. Stop the Shell browser and launcher PIDs exactly; Electron
    // tears down its own renderer/GPU children when the browser process exits.
    const killResults = targetPids.map((pid) => ({ pid, ...killPid(pid, { tree: id !== "shell" }) }));
    const failed = killResults.find((item) => !item.ok);
    if (!failed) {
      delete state.processes[id];
      if (id === "desktop-pet") fs.rmSync(DESKTOP_PET_PROCESS_PATH, { force: true });
      if (id === "shell") {
        const currentShellDescriptor = readShellControlDescriptor();
        if (!currentShellDescriptor || currentShellDescriptor.pid === shellControlDescriptor?.pid) {
          fs.rmSync(SHELL_CONTROL_PATH, { force: true });
        }
      }
    }
    results.push({
      id,
      status: failed ? "stop_failed" : "stopped",
      reason: failed?.reason || killResults.map((item) => item.reason).join(",") || "already_stopped",
      pid: desktopPetDescriptor?.pid || shellControlDescriptor?.pid || record?.pid || verifiedPortOwner?.ownerPid || null,
      verifiedPortOwner: Boolean(verifiedPortOwner),
      matchedBy: verifiedPortOwner?.matchedBy || null,
    });
  }
  writeProcessState(state);
  return results;
}
