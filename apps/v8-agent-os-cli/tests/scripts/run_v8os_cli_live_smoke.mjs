#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const cliRoot = path.resolve(path.dirname(currentFile), "..", "..");
const repoRoot = path.resolve(cliRoot, "..", "..");
const bin = path.join(cliRoot, "bin", "v8os.mjs");
const reportsRoot = path.join(process.env.V8_AGENT_OS_HOME || path.join(process.env.USERPROFILE || process.env.HOME, ".v8-agent-os"), "reports", "cli_base");
const reportDir = path.join(reportsRoot, new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "_"));

function run(args, options = {}) {
  const result = spawnSync(process.execPath, [bin, ...args], {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: options.timeoutMs || 20000,
  });
  return {
    command: `v8os ${args.join(" ")}`,
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
    ok: result.status === 0,
  };
}

async function main() {
  fs.mkdirSync(reportDir, { recursive: true });
  const steps = [];
  steps.push(run(["doctor", "--json"], { timeoutMs: 30000 }));
  steps.push(run(["start"], { timeoutMs: 30000 }));
  await new Promise((resolve) => setTimeout(resolve, 8000));
  steps.push(run(["status", "--json"], { timeoutMs: 15000 }));
  steps.push(run(["config", "mcp", "list", "--json"], { timeoutMs: 15000 }));
  steps.push(run(["config", "models", "doctor"], { timeoutMs: 20000 }));
  steps.push(run(["repair", "--dry-run", "--json"], { timeoutMs: 20000 }));
  steps.push(run(["stop"], { timeoutMs: 30000 }));

  const report = {
    createdAt: new Date().toISOString(),
    repoRoot,
    steps,
  };
  const reportPath = path.join(reportDir, "v8os_cli_live_smoke.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  const failed = steps.filter((step) => !step.ok);
  console.log(`Report: ${reportPath}`);
  if (failed.length) {
    console.error(`Failed steps: ${failed.map((step) => step.command).join(", ")}`);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});
