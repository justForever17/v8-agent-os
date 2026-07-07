#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { DEFAULT_PORTS, REPORTS_DIR, timestampForFile } from "../../src/paths.mjs";
import { getPortOwners, isPortOpen } from "../../src/ports.mjs";

const currentFile = fileURLToPath(import.meta.url);
const cliRoot = path.resolve(path.dirname(currentFile), "..", "..");
const repoRoot = path.resolve(cliRoot, "..", "..");
const bin = path.join(cliRoot, "bin", "v8os.mjs");
const reportDir = path.join(REPORTS_DIR, timestampForFile());

function run(args, options = {}) {
  const result = spawnSync(process.execPath, [bin, ...args], {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: options.timeoutMs || 30000,
  });
  return {
    command: `v8os ${args.join(" ")}`,
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
    ok: result.status === 0,
  };
}

function parseJsonStep(step) {
  try {
    return JSON.parse(step.stdout);
  } catch {
    return null;
  }
}

async function preflightPorts() {
  const ports = [];
  for (const id of ["engine", "admin", "web"]) {
    const port = DEFAULT_PORTS[id];
    const open = await isPortOpen(port, null, 800);
    ports.push({
      id,
      port,
      open,
      owners: open ? getPortOwners(port) : [],
    });
  }
  return ports;
}

async function waitForManaged(steps, timeoutMs = 120000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const step = run(["status", "--json"], { timeoutMs: 15000 });
    steps.push(step);
    const payload = parseJsonStep(step);
    const selected = (payload || []).filter((item) => ["engine", "admin", "web"].includes(item.id));
    const allReady = selected.length === 3 && selected.every((item) => item.state === "managed_running" && item.portOpen);
    if (allReady) return { ok: true, statuses: selected };
    const external = selected.filter((item) => item.state === "external_port_in_use");
    if (external.length) return { ok: false, reason: "external_port_in_use_after_start", statuses: selected };
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  return { ok: false, reason: "timeout_waiting_for_managed_running_ports" };
}

function doctorHasNoFailures(step) {
  const payload = parseJsonStep(step);
  const failed = Number(payload?.summary?.failed || 0);
  const failedChecks = (payload?.checks || []).filter((check) => check.status === "failed");
  return failed === 0 && failedChecks.length === 0;
}

async function main() {
  fs.mkdirSync(reportDir, { recursive: true });
  const report = {
    createdAt: new Date().toISOString(),
    repoRoot,
    preflight: await preflightPorts(),
    steps: [],
    result: null,
  };

  const occupied = report.preflight.filter((item) => item.open);
  if (occupied.length) {
    report.result = { ok: false, reason: "external_port_in_use", occupied };
    const reportPath = path.join(reportDir, "v8os_cli_cold_start.json");
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    console.error(`Cold start blocked by external ports. Report: ${reportPath}`);
    process.exitCode = 2;
    return;
  }

  let shouldStop = false;
  try {
    const start = run(["start", "--json"], { timeoutMs: 45000 });
    report.steps.push(start);
    shouldStop = true;
    if (!start.ok) throw new Error("v8os start failed");

    const waitResult = await waitForManaged(report.steps);
    if (!waitResult.ok) throw new Error(waitResult.reason || "managed start failed");

    const status = run(["status", "--json"], { timeoutMs: 15000 });
    report.steps.push(status);
    if (!status.ok) throw new Error("v8os status failed");

    const doctor = run(["doctor", "--json"], { timeoutMs: 45000 });
    report.steps.push(doctor);
    if (!doctor.ok) throw new Error("v8os doctor failed");
    if (!doctorHasNoFailures(doctor)) throw new Error("v8os doctor reported failed checks");

    report.result = { ok: true, managed: waitResult.statuses };
  } catch (error) {
    report.result = { ok: false, reason: error instanceof Error ? error.message : String(error) };
    process.exitCode = 1;
  } finally {
    if (shouldStop) {
      const stop = run(["stop", "--json"], { timeoutMs: 45000 });
      report.steps.push(stop);
      await new Promise((resolve) => setTimeout(resolve, 2000));
      report.steps.push(run(["status", "--json"], { timeoutMs: 15000 }));
    }
    const reportPath = path.join(reportDir, "v8os_cli_cold_start.json");
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
    console.log(`Report: ${reportPath}`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});
