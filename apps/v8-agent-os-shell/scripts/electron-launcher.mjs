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

export function electronCliPath() {
  const candidate = path.join(desktopPetDir, "node_modules", "electron", "cli.js");
  if (!fs.existsSync(candidate)) {
    throw new Error("Electron CLI is missing. Run npm install in apps/v8-agent-os-desktop-pet.");
  }
  return candidate;
}

export function launchElectron(target, extraEnv = {}) {
  ensureElectron();
  const env = { ...process.env, ...extraEnv };
  delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(process.execPath, [electronCliPath(), target], {
    cwd: repoRoot,
    stdio: "inherit",
    env,
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
  ensureElectron();
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
  return handoff.pid;
}

export const paths = {
  repoRoot,
  shellDir,
  desktopPetDir,
};
