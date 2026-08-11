import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const shellDir = path.resolve(path.dirname(currentFile), "..");
const repoRoot = path.resolve(shellDir, "..", "..");
const desktopPetDir = path.join(repoRoot, "apps", "v8-agent-os-desktop-pet");

export function ensureElectron() {
  const ensureScript = path.join(desktopPetDir, "scripts", "ensure-electron.cjs");
  const result = spawnSync(process.execPath, [ensureScript], {
    cwd: desktopPetDir,
    stdio: "inherit",
    env: process.env,
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error("Electron is not ready. Run v8os doctor or reinstall desktop pet dependencies.");
  }
}

export function electronExecutablePath() {
  const electronRoot = path.join(desktopPetDir, "node_modules", "electron", "dist");
  const candidate = process.platform === "win32"
    ? path.join(electronRoot, "electron.exe")
    : process.platform === "darwin"
      ? path.join(electronRoot, "Electron.app", "Contents", "MacOS", "Electron")
      : path.join(electronRoot, "electron");
  if (!fs.existsSync(candidate)) {
    throw new Error("Electron executable is missing. Run npm install in apps/v8-agent-os-desktop-pet.");
  }
  return candidate;
}

export function isPackagedShellRuntime(env = process.env) {
  return env.V8OS_SHELL_PACKAGED === "1";
}

export function desktopRuntimeSpawnSpec(target, extraEnv = {}) {
  const env = { ...process.env, ...extraEnv };
  delete env.ELECTRON_RUN_AS_NODE;
  env.V8_REPO_ROOT = paths.repoRoot;
  env.V8_DESKTOP_PET_DIR = paths.desktopPetDir;
  env.V8OS_DESKTOP_RUNTIME_MODE = "desktop-pet";

  if (isPackagedShellRuntime(env)) {
    const shellExecutable = String(env.V8OS_SHELL_EXECUTABLE || process.execPath).trim();
    if (!shellExecutable || !fs.existsSync(shellExecutable)) {
      throw new Error("Packaged V8OS Shell executable is unavailable for the desktop pet runtime.");
    }
    env.V8_DESKTOP_NODE = shellExecutable;
    env.V8_DESKTOP_NODE_IS_ELECTRON = "1";
    return {
      command: shellExecutable,
      args: [target],
      cwd: paths.repoRoot,
      env,
    };
  }

  ensureElectron();
  const electronExecutable = electronExecutablePath();
  env.V8_DESKTOP_NODE = electronExecutable;
  env.V8_DESKTOP_NODE_IS_ELECTRON = "1";
  return {
    command: electronExecutable,
    args: [target],
    cwd: paths.repoRoot,
    env,
  };
}

export function shellRuntimeSpawnSpec(target, extraEnv = {}) {
  const env = { ...process.env, ...extraEnv };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.V8OS_DESKTOP_RUNTIME_MODE;

  if (isPackagedShellRuntime(env)) {
    const shellExecutable = String(env.V8OS_SHELL_EXECUTABLE || process.execPath).trim();
    if (!shellExecutable || !fs.existsSync(shellExecutable)) {
      throw new Error("Packaged V8OS Shell executable is unavailable.");
    }
    return {
      command: shellExecutable,
      args: [],
      cwd: paths.repoRoot,
      env,
    };
  }

  ensureElectron();
  return {
    command: electronExecutablePath(),
    args: [target],
    cwd: paths.repoRoot,
    env,
  };
}

export function launchElectron(target, extraEnv = {}) {
  const spec = shellRuntimeSpawnSpec(target, extraEnv);
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    stdio: "inherit",
    env: spec.env,
    windowsHide: true,
  });
  const stop = () => {
    try {
      child.kill();
    } catch {}
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code ?? 0);
  });
}

export async function launchDetachedElectron(target, extraEnv = {}) {
  if (!isPackagedShellRuntime()) ensureElectron();
  const handoffPath = path.join(os.tmpdir(), `v8os-desktop-pet-${process.pid}-${Date.now()}.json`);
  const interposer = path.join(shellDir, "scripts", "spawn-detached-electron.mjs");
  const env = { ...process.env, ...extraEnv };
  if (process.versions?.electron) env.ELECTRON_RUN_AS_NODE = "1";
  const launcher = spawn(process.execPath, [interposer, target, handoffPath], {
    cwd: repoRoot,
    stdio: "inherit",
    env,
    windowsHide: true,
  });
  const exitCode = await new Promise((resolve) => launcher.on("exit", (code) => resolve(code ?? 1)));
  if (exitCode !== 0 || !fs.existsSync(handoffPath)) {
    fs.rmSync(handoffPath, { force: true });
    throw new Error("Detached Electron launcher failed before reporting the desktop pet PID.");
  }
  const handoff = JSON.parse(fs.readFileSync(handoffPath, "utf8"));
  fs.rmSync(handoffPath, { force: true });
  if (!Number.isInteger(handoff?.pid) || handoff.pid <= 0) {
    throw new Error("Detached Electron launcher returned an invalid desktop pet PID.");
  }
  writeManagedRuntimeHandoff(handoff.pid);
  return handoff.pid;
}

function writeManagedRuntimeHandoff(pid) {
  const receiptPath = String(process.env.V8OS_RUNTIME_HANDOFF_PATH || "").trim();
  const nonce = String(process.env.V8OS_RUNTIME_HANDOFF_NONCE || "").trim();
  if (!receiptPath && !nonce) return;
  if (!receiptPath || !/^[0-9a-f-]{36}$/i.test(nonce)) {
    throw new Error("Managed desktop pet handoff contract is incomplete.");
  }
  const stateRoot = path.resolve(process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os"));
  const handoffRoot = path.join(stateRoot, "runtime", "cli", "handoffs");
  const expectedPath = path.join(handoffRoot, `desktop-pet-${nonce}.json`);
  if (path.resolve(receiptPath) !== expectedPath) {
    throw new Error("Managed desktop pet handoff path is outside the governed runtime directory.");
  }
  fs.mkdirSync(handoffRoot, { recursive: true });
  const temporaryPath = `${expectedPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify({ version: 1, componentId: "desktop-pet", nonce, pid })}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  fs.renameSync(temporaryPath, expectedPath);
}

export const paths = {
  repoRoot,
  shellDir,
  desktopPetDir,
};
