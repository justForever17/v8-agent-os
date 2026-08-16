#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(__filename), "..", "..");
const driverDir = path.join(repoRoot, "apps", "v8-agent-os-engine", "runtimes", "computer_use", "drivers");
const source = path.join(driverDir, "mac_ax_helper.swift");
const arch = process.arch === "arm64" ? "arm64" : process.arch === "x64" ? "x64" : "";
const output = arch ? path.join(driverDir, "bin", `macos-${arch}`, "mac_ax_helper") : "";
const deploymentTarget = String(process.env.MACOSX_DEPLOYMENT_TARGET || "12.0").trim();

try {
  if (process.platform !== "darwin" || !arch) {
    throw new Error(`macOS AX helper requires a native macOS x64 or arm64 runner; got ${process.platform}/${process.arch}.`);
  }
  if (!/^\d+\.\d+$/.test(deploymentTarget)) {
    throw new Error(`Invalid MACOSX_DEPLOYMENT_TARGET: ${deploymentTarget}`);
  }
  if (!fs.existsSync(source)) throw new Error(`Missing macOS AX helper source: ${source}`);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const targetArch = arch === "x64" ? "x86_64" : "arm64";
  const result = spawnSync(
    "swiftc",
    ["-target", `${targetArch}-apple-macosx${deploymentTarget}`, source, "-O", "-o", output],
    { encoding: "utf8", timeout: 120000 },
  );
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.error || result.status !== 0 || !fs.existsSync(output)) {
    throw new Error(`swiftc could not build the packaged macOS AX helper: ${result.error?.message || `exit ${result.status}`}`);
  }
  fs.chmodSync(output, 0o755);
  console.log(`Built packaged macOS AX helper: ${output}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
