#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));
const DMG_DETACH_BUSY = /dmgbuild\.core\.DMGError:\s*Unable to detach device cleanly:\s*hdiutil:\s*couldn['’]t eject ["'](?:\/dev\/)?disk\d+(?:s\d+)*["']\s*-\s*Resource busy\b/i;

export function isRetryableDmgDetachFailure(result) {
  if (result.status !== 1 || result.signal || result.error) return false;
  return DMG_DETACH_BUSY.test(`${result.stdout || ""}\n${result.stderr || ""}`);
}

export async function runMacDmgBuildWithRetry({
  arch,
  platform = process.platform,
  spawn = spawnSync,
  sleep = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)),
} = {}) {
  if (platform !== "darwin" || !["x64", "arm64"].includes(arch)) {
    throw new Error("DMG build requires macOS and exactly one x64 or arm64 target.");
  }
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    const result = spawn("npm", ["--prefix", "apps/v8-agent-os-shell", "run", "dist:mac:preview", "--", `--${arch}`], {
      cwd: REPO_ROOT,
      env: process.env,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      windowsHide: true,
      stdio: ["inherit", "pipe", "pipe"],
    });
    if (result.stdout) process.stdout.write(String(result.stdout));
    if (result.stderr) process.stderr.write(String(result.stderr));
    if (result.error) process.stderr.write(`[v8os dmg] ${result.error.message}\n`);
    if (result.status === 0 && !result.error && !result.signal) return 0;
    if (attempt === 2 || !isRetryableDmgDetachFailure(result)) {
      return Number.isInteger(result.status) && result.status > 0 ? result.status : 1;
    }
    // dmgbuild owns its temporary image and detach cleanup. Never derive a
    // disk target from stderr or force-eject unrelated host volumes here.
    process.stderr.write("[v8os dmg] temporary image detach was busy; retrying this DMG build once in 10000ms.\n");
    await sleep(10_000);
  }
  return 1;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  if (process.argv.length !== 3) throw new Error("Usage: build-macos-dmg-with-retry.mjs <x64|arm64>");
  process.exitCode = await runMacDmgBuildWithRetry({ arch: process.argv[2] });
}
