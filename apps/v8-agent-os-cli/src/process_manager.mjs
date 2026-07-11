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

export function stopComponents(componentIds = Object.keys(COMPONENTS)) {
  const state = readProcessState();
  const results = [];
  for (const id of componentIds) {
    const record = state.processes[id];
    const desktopPetDescriptor = id === "desktop-pet" ? readDesktopPetProcessDescriptor() : null;
    const shellControlDescriptor = id === "shell" ? readShellControlDescriptor() : null;
    if (!record && !desktopPetDescriptor && !shellControlDescriptor) {
      results.push({ id, status: "not_managed" });
      continue;
    }
    const targetPids = [...new Set([
      desktopPetDescriptor?.pid,
      shellControlDescriptor?.pid,
      record?.pid,
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
      pid: desktopPetDescriptor?.pid || shellControlDescriptor?.pid || record?.pid || null,
    });
  }
  writeProcessState(state);
  return results;
}
