import fs from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import { ensureDir } from "./json_file.mjs";
import { COMPONENTS, logPathsFor } from "./components.mjs";
import { LOG_DIR } from "./paths.mjs";
import { isPortOpen } from "./ports.mjs";
import { isPidAlive, readProcessState, writeProcessState } from "./process_state.mjs";

function componentHasPort(component) {
  return Number.isInteger(component?.port) && component.port > 0;
}

export async function statusComponents(componentIds = Object.keys(COMPONENTS)) {
  const state = readProcessState();
  const statuses = [];
  for (const id of componentIds) {
    const component = COMPONENTS[id];
    if (!component) continue;
    const record = state.processes[id] || null;
    const pidAlive = record?.pid ? isPidAlive(record.pid) : false;
    const hasPort = componentHasPort(component);
    const portOpen = hasPort ? await isPortOpen(component.port) : false;
    statuses.push({
      id,
      label: component.label,
      port: component.port,
      managed: Boolean(record && record.managed),
      pid: record?.pid || null,
      pidAlive,
      portOpen,
      state: pidAlive ? "managed_running" : hasPort && portOpen ? "external_port_in_use" : "stopped",
      startedAt: record?.startedAt || null,
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
    if (record?.pid && isPidAlive(record.pid)) {
      results.push({ id, status: "already_running", pid: record.pid });
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

function killPid(pid) {
  const numeric = Number(pid);
  if (!Number.isInteger(numeric) || numeric <= 0) return { ok: false, reason: "invalid_pid" };
  if (!isPidAlive(numeric)) return { ok: true, reason: "already_stopped" };
  if (process.platform === "win32") {
    const result = spawnSync("taskkill", ["/PID", String(numeric), "/T", "/F"], { encoding: "utf8" });
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
    if (!record) {
      results.push({ id, status: "not_managed" });
      continue;
    }
    const killed = killPid(record.pid);
    if (killed.ok) {
      delete state.processes[id];
    }
    results.push({ id, status: killed.ok ? "stopped" : "stop_failed", reason: killed.reason, pid: record.pid });
  }
  writeProcessState(state);
  return results;
}
