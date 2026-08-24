#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const DEFAULT_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [10_000, 30_000];
const RETRYABLE_NETWORK_ERROR = /\b(?:ECONNRESET|ECONNABORTED|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|ECONNREFUSED|ENOTFOUND|ERR_SOCKET_TIMEOUT)\b|network\s+(?:aborted|timeout|request failed)|socket hang up/i;

function writeCaptured(result) {
  if (result.stdout) process.stdout.write(String(result.stdout));
  if (result.stderr) process.stderr.write(String(result.stderr));
  if (result.error) process.stderr.write(`[v8os npm-ci] ${result.error.message}\n`);
}

export function isRetryableNpmNetworkFailure(result) {
  const output = `${result.stdout || ""}\n${result.stderr || ""}\n${result.error?.message || ""}`;
  return RETRYABLE_NETWORK_ERROR.test(output);
}

export async function runNpmCiWithRetry({
  attempts = DEFAULT_ATTEMPTS,
  platform = process.platform,
  comspec = process.env.ComSpec || "cmd.exe",
  spawn = spawnSync,
  sleep = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)),
} = {}) {
  const boundedAttempts = Math.max(1, Math.min(Number(attempts) || DEFAULT_ATTEMPTS, DEFAULT_ATTEMPTS));
  const npmArgs = ["ci", "--include=dev", "--workspaces=false"];
  const command = platform === "win32" ? comspec : "npm";
  const commandArgs = platform === "win32"
    ? ["/d", "/s", "/c", `npm ${npmArgs.join(" ")}`]
    : npmArgs;

  for (let attempt = 1; attempt <= boundedAttempts; attempt += 1) {
    const result = spawn(command, commandArgs, {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      windowsHide: true,
      stdio: ["inherit", "pipe", "pipe"],
    });
    writeCaptured(result);
    if (result.status === 0) return 0;

    const retryable = isRetryableNpmNetworkFailure(result);
    if (!retryable || attempt >= boundedAttempts) {
      return Number.isInteger(result.status) && result.status > 0 ? result.status : 1;
    }

    const delayMs = RETRY_DELAYS_MS[attempt - 1] || RETRY_DELAYS_MS.at(-1);
    process.stderr.write(
      `[v8os npm-ci] network failure on attempt ${attempt}/${boundedAttempts}; retrying in ${delayMs}ms.\n`,
    );
    await sleep(delayMs);
  }
  return 1;
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (invokedPath === import.meta.url) {
  process.exitCode = await runNpmCiWithRetry();
}
