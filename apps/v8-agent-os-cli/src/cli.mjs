import { spawn } from "node:child_process";
import { ALL_COMPONENTS, parseComponentSelection } from "./components.mjs";
import { getConfigDomain, listConfigDomains, listMcpServers, modelRoleDoctor, pairingSummary } from "./config_commands.mjs";
import { runDoctor } from "./doctor.mjs";
import { runRepair } from "./repair.mjs";
import { DEFAULT_PORTS, LOG_DIR, REPO_ROOT } from "./paths.mjs";
import { startComponents, statusComponents, stopComponents } from "./process_manager.mjs";
import { printJson, renderConfigDomains, renderDoctor, renderMcpServers, renderStartResults, renderStatus } from "./render.mjs";

function hasFlag(args, flag) {
  return args.includes(flag);
}

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function help() {
  console.log(`V8OS CLI

Usage:
  v8os [start]
  v8os start [--with cybercore|--all|--only engine,admin] [--mode dev|start]
  v8os stop [--all|--only engine,admin]
  v8os restart [--all|--only engine,admin]
  v8os status [--json]
  v8os doctor [--json]
  v8os config list|get <domain>|mcp list|models doctor|pairing show [--json]
  v8os repair [--dry-run|--yes] [--json]
  v8os logs
  v8os open admin|web
`);
}

async function commandStart(args) {
  const mode = optionValue(args, "--mode", "dev");
  const selected = parseComponentSelection(args);
  const results = await startComponents(selected, { mode });
  if (hasFlag(args, "--json")) printJson(results);
  else renderStartResults(results);
}

async function commandStop(args) {
  const selected = hasFlag(args, "--all") ? ALL_COMPONENTS : parseComponentSelection(args);
  const results = stopComponents(selected);
  if (hasFlag(args, "--json")) printJson(results);
  else results.forEach((item) => console.log(`${item.id}: ${item.status}${item.reason ? ` (${item.reason})` : ""}`));
}

async function commandStatus(args) {
  const statuses = await statusComponents(ALL_COMPONENTS);
  if (hasFlag(args, "--json")) printJson(statuses);
  else renderStatus(statuses);
}

async function commandDoctor(args) {
  const payload = await runDoctor();
  if (hasFlag(args, "--json")) printJson(payload);
  else renderDoctor(payload);
}

async function commandConfig(args) {
  const sub = args[0] || "list";
  const json = hasFlag(args, "--json");
  if (sub === "list") {
    const result = await listConfigDomains();
    json ? printJson(result) : renderConfigDomains(result);
    return;
  }
  if (sub === "get") {
    const domain = args[1];
    if (!domain) throw new Error("config get requires a domain");
    const result = await getConfigDomain(domain);
    printJson(result);
    return;
  }
  if (sub === "mcp" && args[1] === "list") {
    const result = await listMcpServers();
    json ? printJson(result) : renderMcpServers(result);
    return;
  }
  if (sub === "models" && args[1] === "doctor") {
    printJson(await modelRoleDoctor());
    return;
  }
  if (sub === "pairing" && args[1] === "show") {
    printJson(await pairingSummary());
    return;
  }
  throw new Error(`Unknown config command: ${args.join(" ")}`);
}

async function commandRepair(args) {
  const dryRun = hasFlag(args, "--dry-run") || !hasFlag(args, "--yes");
  const result = await runRepair({ dryRun, yes: hasFlag(args, "--yes") });
  if (hasFlag(args, "--json")) printJson(result);
  else {
    console.log(`Repair ${dryRun ? "dry-run" : "run"} complete.`);
    for (const item of result.applied) console.log(`- ${item.title} ${dryRun ? "(dry-run)" : ""}`);
    for (const item of result.skipped) console.log(`- skipped: ${item.title} (${item.reason})`);
  }
}

function commandLogs() {
  console.log(LOG_DIR);
}

function commandOpen(args) {
  const target = args[0] || "admin";
  const url = target === "web" ? `http://127.0.0.1:${DEFAULT_PORTS.web}/chat` : `http://127.0.0.1:${DEFAULT_PORTS.admin}/admin`;
  const command = process.platform === "win32" ? "cmd" : process.platform === "darwin" ? "open" : "xdg-open";
  const commandArgs = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  spawn(command, commandArgs, { detached: true, stdio: "ignore" }).unref();
  console.log(url);
}

export async function main(argv) {
  const args = [...argv];
  const command = args.shift() || "start";
  if (command === "-h" || command === "--help" || command === "help") {
    help();
    return;
  }
  if (command === "start") return commandStart(args);
  if (command === "stop") return commandStop(args);
  if (command === "restart") {
    await commandStop(args);
    return commandStart(args);
  }
  if (command === "status") return commandStatus(args);
  if (command === "doctor") return commandDoctor(args);
  if (command === "config") return commandConfig(args);
  if (command === "repair") return commandRepair(args);
  if (command === "logs") return commandLogs(args);
  if (command === "open") return commandOpen(args);
  throw new Error(`Unknown command "${command}". Run "v8os help". Repo: ${REPO_ROOT}`);
}
