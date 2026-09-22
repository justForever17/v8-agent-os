import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const shellDir = path.resolve(path.dirname(currentFile), "..");
const repoRoot = path.resolve(shellDir, "..", "..");
const desktopPetDir = path.join(repoRoot, "apps", "v8-agent-os-desktop-pet");

export function ensureElectron() {
  const ensureScript = path.join(repoRoot, "scripts", "desktop", "ensure-electron-runtime.mjs");
  const result = spawnSync(process.execPath, [ensureScript, "--package-root", shellDir], {
    cwd: shellDir,
    stdio: "inherit",
    env: process.env,
    windowsHide: true,
  });
  if (result.status !== 0) {
    throw new Error("Electron is not ready. Run v8os doctor or reinstall Shell dependencies.");
  }
}

export function electronExecutablePath() {
  const electronRoot = path.join(shellDir, "node_modules", "electron", "dist");
  const candidate = process.platform === "win32"
    ? path.join(electronRoot, "electron.exe")
    : process.platform === "darwin"
      ? path.join(electronRoot, "Electron.app", "Contents", "MacOS", "Electron")
      : path.join(electronRoot, "electron");
  if (!fs.existsSync(candidate)) {
    throw new Error("Electron executable is missing. Run npm install in apps/v8-agent-os-shell.");
  }
  return candidate;
}

export function isPackagedShellRuntime(env = process.env) {
  return env.V8OS_SHELL_PACKAGED === "1";
}

function inheritedElectronArgs(env) {
  return env.V8OS_ELECTRON_NO_SANDBOX === "1" ? ["--no-sandbox"] : [];
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
      args: inheritedElectronArgs(env),
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

export const paths = {
  repoRoot,
  shellDir,
  desktopPetDir,
};
