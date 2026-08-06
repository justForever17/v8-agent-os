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

export const MANAGED_COMPONENT_IDS = Object.freeze(["engine", "admin", "web"]);

const PROBE_DEFINITIONS = Object.freeze([
  {
    id: "engine_ready",
    label: "Engine /readyz",
    url: `http://127.0.0.1:${DEFAULT_PORTS.engine}/readyz`,
    kind: "engine",
  },
  {
    id: "admin_http_ready",
    label: "Admin login HTTP",
    url: `http://127.0.0.1:${DEFAULT_PORTS.admin}/login`,
    kind: "admin",
  },
  {
    id: "web_http_ready",
    label: "Web chat HTTP",
    url: `http://127.0.0.1:${DEFAULT_PORTS.web}/chat`,
    kind: "web",
  },
]);

const BUDGET_STAGE_IDS = Object.freeze({
  engineReadyMs: "engine_ready",
  adminReadyMs: "admin_http_ready",
  webReadyMs: "web_http_ready",
  allReadyMs: "all_ready",
});

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function optionalPositiveInteger(args, name) {
  if (!args.includes(name)) return null;
  const value = Number(optionValue(args, name));
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

export function parseOptions(args = process.argv.slice(2)) {
  const knownFlags = new Set([
    "--json",
    "--help",
    "--mode",
    "--timeout-ms",
    "--engine-ready-budget-ms",
    "--admin-ready-budget-ms",
    "--web-ready-budget-ms",
    "--all-ready-budget-ms",
  ]);
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
    if (!knownFlags.has(value)) throw new Error(`Unknown option: ${value}`);
    if (!["--json", "--help"].includes(value)) index += 1;
  }

  const mode = optionValue(args, "--mode", "start");
  if (!["dev", "start"].includes(mode)) throw new Error("--mode must be dev or start");

  return {
    help: args.includes("--help"),
    json: args.includes("--json"),
    mode,
    timeoutMs: optionalPositiveInteger(args, "--timeout-ms") || 120_000,
    budgets: {
      engineReadyMs: optionalPositiveInteger(args, "--engine-ready-budget-ms"),
      adminReadyMs: optionalPositiveInteger(args, "--admin-ready-budget-ms"),
      webReadyMs: optionalPositiveInteger(args, "--web-ready-budget-ms"),
      allReadyMs: optionalPositiveInteger(args, "--all-ready-budget-ms"),
    },
  };
}

function help() {
  console.log(`V8OS cold-start smoke

Usage:
  node tests/scripts/run_v8os_cli_cold_start_smoke.mjs [options]

Options:
  --mode dev|start                 Service mode (default: start)
  --timeout-ms <ms>                End-to-end readiness timeout (default: 120000)
  --engine-ready-budget-ms <ms>    Optional Engine readiness regression budget
  --admin-ready-budget-ms <ms>     Optional Admin HTTP regression budget
  --web-ready-budget-ms <ms>       Optional Web HTTP regression budget
  --all-ready-budget-ms <ms>       Optional all-surfaces regression budget
  --json                           Print the complete report to stdout
`);
}

function run(args, options = {}) {
  const startedAtMs = Date.now();
  const result = spawnSync(process.execPath, [bin, ...args], {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: options.timeoutMs || 30_000,
    windowsHide: true,
  });
  const completedAtMs = Date.now();
  return {
    command: `v8os ${args.join(" ")}`,
    startedAt: new Date(startedAtMs).toISOString(),
    completedAt: new Date(completedAtMs).toISOString(),
    durationMs: completedAtMs - startedAtMs,
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
    error: result.error?.message || null,
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
  for (const id of MANAGED_COMPONENT_IDS) {
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

export function validateProbeResponse(kind, response) {
  if (!response.ok || response.status < 200 || response.status >= 300) {
    return { ok: false, reason: `http_${response.status}` };
  }

  let finalUrl;
  let expectedUrl;
  try {
    finalUrl = new URL(String(response.responseUrl || ""));
    expectedUrl = new URL(String(response.expectedUrl || ""));
  } catch {
    return { ok: false, reason: `${kind}_response_url_invalid` };
  }
  if (finalUrl.origin !== expectedUrl.origin) {
    return { ok: false, reason: `${kind}_redirect_origin_mismatch` };
  }
  const pathMatches = kind === "engine"
    ? finalUrl.pathname === "/readyz"
    : kind === "admin"
      ? finalUrl.pathname === "/login"
      : kind === "web"
        ? finalUrl.pathname === "/chat" || finalUrl.pathname.startsWith("/chat/")
        : false;
  if (!pathMatches) {
    return { ok: false, reason: `${kind}_response_path_mismatch` };
  }

  if (kind === "engine") {
    if (!String(response.contentType || "").toLowerCase().includes("application/json")) {
      return { ok: false, reason: "unexpected_content_type" };
    }
    let payload;
    try {
      payload = JSON.parse(response.body);
    } catch {
      return { ok: false, reason: "invalid_json" };
    }
    if (payload?.status !== "ok" || payload?.service !== "v8-agent-os-engine" || payload?.ready !== true) {
      return { ok: false, reason: "readiness_contract_mismatch" };
    }
    return {
      ok: true,
      marker: "v8-agent-os-engine:ready",
      startup: payload.startup && typeof payload.startup === "object" ? payload.startup : null,
    };
  }

  const body = String(response.body || "");
  if (kind === "admin") {
    return body.includes('id="login"')
      ? { ok: true, marker: 'id="login"' }
      : { ok: false, reason: "admin_login_marker_missing" };
  }
  if (kind === "web") {
    return body.includes("V8 Agent OS - AI Assistant")
      ? { ok: true, marker: "V8 Agent OS - AI Assistant" }
      : { ok: false, reason: "web_chat_marker_missing" };
  }
  return { ok: false, reason: "unknown_probe_kind" };
}

async function probeOnce(definition, requestTimeoutMs = 2_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
  const startedAtMs = Date.now();
  try {
    const response = await fetch(definition.url, {
      cache: "no-store",
      redirect: "follow",
      signal: controller.signal,
    });
    const body = await response.text();
    const validation = validateProbeResponse(definition.kind, {
      ok: response.ok,
      status: response.status,
      contentType: response.headers.get("content-type") || "",
      body,
      expectedUrl: definition.url,
      responseUrl: response.url,
    });
    return {
      ...validation,
      status: response.status,
      responseUrl: response.url,
      requestMs: Date.now() - startedAtMs,
    };
  } catch (error) {
    return {
      ok: false,
      reason: error?.name === "AbortError" ? "request_timeout" : "request_failed",
      error: error instanceof Error ? error.message : String(error),
      requestMs: Date.now() - startedAtMs,
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function waitForHttpProbe(definition, options) {
  const originMs = options.originMs;
  const deadlineMs = originMs + options.timeoutMs;
  const intervalMs = options.intervalMs || 250;
  let attempts = 0;
  let lastObservation = null;

  while (Date.now() < deadlineMs) {
    attempts += 1;
    const remainingMs = Math.max(1, deadlineMs - Date.now());
    lastObservation = await probeOnce(definition, Math.min(2_000, remainingMs));
    if (lastObservation.ok) {
      return {
        id: definition.id,
        label: definition.label,
        url: definition.url,
        ok: true,
        elapsedMs: Date.now() - originMs,
        attempts,
        ...lastObservation,
      };
    }
    const delayMs = Math.min(intervalMs, Math.max(0, deadlineMs - Date.now()));
    if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
  }

  return {
    id: definition.id,
    label: definition.label,
    url: definition.url,
    ok: false,
    elapsedMs: Date.now() - originMs,
    attempts,
    reason: "readiness_timeout",
    lastObservation,
  };
}

function parseStartedComponents(startStep) {
  const payload = parseJsonStep(startStep);
  if (!Array.isArray(payload)) return { ok: false, reason: "invalid_start_json", components: [] };
  const components = MANAGED_COMPONENT_IDS.map((id) => payload.find((item) => item?.id === id) || null);
  if (components.some((item) => !item)) return { ok: false, reason: "missing_start_component", components };
  const rejected = components.filter((item) => item.status !== "started");
  if (rejected.length) return { ok: false, reason: "component_not_started", components, rejected };
  return { ok: true, components };
}

async function waitForStopped(steps, timeoutMs = 15_000) {
  const startedAtMs = Date.now();
  let statuses = [];
  while (Date.now() - startedAtMs < timeoutMs) {
    const step = run(["status", "--json"], { timeoutMs: 15_000 });
    steps.push(step);
    const payload = parseJsonStep(step);
    statuses = Array.isArray(payload)
      ? payload.filter((item) => MANAGED_COMPONENT_IDS.includes(item.id))
      : [];
    const allStopped = statuses.length === MANAGED_COMPONENT_IDS.length
      && statuses.every((item) => item.state === "stopped" && !item.portOpen);
    if (allStopped) return { ok: true, statuses, elapsedMs: Date.now() - startedAtMs };
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return { ok: false, reason: "timeout_waiting_for_cleanup", statuses, elapsedMs: Date.now() - startedAtMs };
}

function doctorHasNoFailures(step) {
  const payload = parseJsonStep(step);
  const failed = Number(payload?.summary?.failed || 0);
  const failedChecks = (payload?.checks || []).filter((check) => check.status === "failed");
  return failed === 0 && failedChecks.length === 0;
}

export function evaluateStageBudgets(stages, budgets) {
  const byId = new Map(stages.map((stage) => [stage.id, stage]));
  return Object.entries(BUDGET_STAGE_IDS)
    .filter(([budgetKey]) => Number.isSafeInteger(budgets[budgetKey]))
    .map(([budgetKey, stageId]) => {
      const stage = byId.get(stageId);
      const elapsedMs = Number(stage?.elapsedMs);
      const budgetMs = budgets[budgetKey];
      return {
        budgetKey,
        stageId,
        budgetMs,
        elapsedMs: Number.isFinite(elapsedMs) ? elapsedMs : null,
        ok: Boolean(stage?.ok) && Number.isFinite(elapsedMs) && elapsedMs <= budgetMs,
      };
    });
}

function writeReport(reportDir, report) {
  const reportPath = path.join(reportDir, "v8os_cli_cold_start.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

export async function main(options = parseOptions()) {
  if (options.help) {
    help();
    return;
  }

  const reportDir = path.join(REPORTS_DIR, timestampForFile());
  fs.mkdirSync(reportDir, { recursive: true });
  const reportCreatedAtMs = Date.now();
  const preflightStartedAtMs = Date.now();
  const preflightStatus = run(["status", "--json"], { timeoutMs: 15_000 });
  const preflightStatusPayload = parseJsonStep(preflightStatus);
  const preflightStatuses = Array.isArray(preflightStatusPayload)
    ? preflightStatusPayload.filter((item) => MANAGED_COMPONENT_IDS.includes(item.id))
    : [];
  const preflightPortsSnapshot = await preflightPorts();
  const preflightCompletedAtMs = Date.now();
  const report = {
    schemaVersion: 2,
    createdAt: new Date(reportCreatedAtMs).toISOString(),
    repoRoot,
    mode: options.mode,
    timeoutMs: options.timeoutMs,
    budgets: options.budgets,
    preflight: {
      startedAt: new Date(preflightStartedAtMs).toISOString(),
      completedAt: new Date(preflightCompletedAtMs).toISOString(),
      durationMs: preflightCompletedAtMs - preflightStartedAtMs,
      ports: preflightPortsSnapshot,
      statuses: preflightStatuses,
    },
    startupStartedAt: null,
    stages: [],
    performance: { checks: [], ok: true },
    steps: [preflightStatus],
    cleanup: null,
    result: null,
  };

  const occupied = report.preflight.ports.filter((item) => item.open);
  const activeBeforeStart = preflightStatuses.filter((item) => item.state !== "stopped");
  if (!preflightStatus.ok || preflightStatuses.length !== MANAGED_COMPONENT_IDS.length) {
    report.result = { ok: false, reason: "preflight_status_failed" };
    const reportPath = writeReport(reportDir, report);
    if (options.json) console.log(JSON.stringify({ reportPath, ...report }, null, 2));
    else console.error(`Cold start preflight status failed. Report: ${reportPath}`);
    process.exitCode = 2;
    return report;
  }
  if (occupied.length || activeBeforeStart.length) {
    report.result = {
      ok: false,
      reason: activeBeforeStart.length ? "managed_process_active_before_start" : "port_in_use_before_start",
      occupied,
      activeBeforeStart,
    };
    const reportPath = writeReport(reportDir, report);
    if (options.json) console.log(JSON.stringify({ reportPath, ...report }, null, 2));
    else console.error(`Cold start blocked by an active process or occupied port. Report: ${reportPath}`);
    process.exitCode = 2;
    return report;
  }

  let shouldStop = false;
  try {
    shouldStop = true;
    const originMs = Date.now();
    report.startupStartedAt = new Date(originMs).toISOString();
    const start = run(["start", "--mode", options.mode, "--json"], { timeoutMs: 45_000 });
    report.steps.push(start);
    if (!start.ok) throw new Error("v8os start failed");

    const started = parseStartedComponents(start);
    if (!started.ok) throw new Error(started.reason || "managed spawn failed");
    const engine = started.components.find((item) => item.id === "engine");
    report.stages.push({
      id: "engine_spawn",
      label: "Engine managed process spawned",
      ok: true,
      elapsedMs: Date.now() - originMs,
      pid: engine.pid,
      launchId: engine.launchId || null,
    });

    const probes = await Promise.all(PROBE_DEFINITIONS.map((definition) => waitForHttpProbe(definition, {
      originMs,
      timeoutMs: options.timeoutMs,
    })));
    report.stages.push(...probes);
    const failedProbe = probes.find((probe) => !probe.ok);
    if (failedProbe) throw new Error(`${failedProbe.id}:${failedProbe.reason}`);

    const allReadyMs = Math.max(...probes.map((probe) => probe.elapsedMs));
    report.stages.push({ id: "all_ready", label: "All local HTTP surfaces ready", ok: true, elapsedMs: allReadyMs });

    const status = run(["status", "--json"], { timeoutMs: 15_000 });
    report.steps.push(status);
    if (!status.ok) throw new Error("v8os status failed");
    const statusPayload = parseJsonStep(status);
    const managed = Array.isArray(statusPayload)
      ? statusPayload.filter((item) => MANAGED_COMPONENT_IDS.includes(item.id))
      : [];
    if (managed.length !== MANAGED_COMPONENT_IDS.length
      || managed.some((item) => item.state !== "managed_running" || !item.portOpen)) {
      throw new Error("managed_process_contract_failed");
    }

    const doctor = run(["doctor", "--json"], { timeoutMs: 45_000 });
    report.steps.push(doctor);
    if (!doctor.ok) throw new Error("v8os doctor failed");
    if (!doctorHasNoFailures(doctor)) throw new Error("v8os doctor reported failed checks");

    report.performance.checks = evaluateStageBudgets(report.stages, options.budgets);
    report.performance.ok = report.performance.checks.every((check) => check.ok);
    if (!report.performance.ok) throw new Error("cold_start_budget_exceeded");

    report.result = { ok: true, managed, allReadyMs };
  } catch (error) {
    report.result = { ok: false, reason: error instanceof Error ? error.message : String(error) };
    process.exitCode = 1;
  } finally {
    if (shouldStop) {
      const stop = run(["stop", "--only", MANAGED_COMPONENT_IDS.join(","), "--json"], { timeoutMs: 45_000 });
      report.steps.push(stop);
      const stopped = await waitForStopped(report.steps);
      report.cleanup = { stopCommandOk: stop.ok, ...stopped };
      if (!stop.ok || !stopped.ok) {
        report.result = {
          ok: false,
          reason: report.result?.ok ? "cleanup_failed" : report.result?.reason || "cleanup_failed",
          cleanupReason: stopped.reason || (stop.ok ? null : "v8os_stop_failed"),
        };
        process.exitCode = 1;
      }
    }
    const reportPath = writeReport(reportDir, report);
    if (options.json) console.log(JSON.stringify({ reportPath, ...report }, null, 2));
    else console.log(`Report: ${reportPath}`);
  }
  return report;
}

const isDirectRun = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(currentFile);
if (isDirectRun) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack || error.message : String(error));
    process.exitCode = 1;
  });
}
