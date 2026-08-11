import { spawn } from "node:child_process";
import { ALL_COMPONENTS, parseComponentSelection } from "./components.mjs";
import { interactiveChat, sendChatMessage } from "./chat_commands.mjs";
import {
  getConfigDomain,
  installMcpServer,
  listConfigDomains,
  listMcpServers,
  mcpStatus,
  modelRoleDoctor,
  modelInventory,
  modelRoles,
  phonePairingManifest,
  phonePairingSummary,
  removeMcpServer,
  recommendModel,
  setModelRole,
} from "./config_commands.mjs";
import { runDoctor } from "./doctor.mjs";
import { commandInbox } from "./inbox_commands.mjs";
import { commandPreview } from "./preview_commands.mjs";
import { runRepair } from "./repair.mjs";
import { LOG_DIR, REPO_ROOT } from "./paths.mjs";
import { startComponents, statusComponents, stopComponents } from "./process_manager.mjs";
import { commandSessions } from "./session_commands.mjs";
import { commandWorkspace } from "./workspace_commands.mjs";
import { readRuntimePorts } from "./runtime_ports.mjs";
import {
  printJson,
  renderConfigDomains,
  renderDoctor,
  renderMcpServers,
  renderMcpStatus,
  renderModelRoles,
  renderPhoneManifest,
  renderStartResults,
  renderStatus,
} from "./render.mjs";

function hasFlag(args, flag) {
  return args.includes(flag);
}

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

const SUCCESS_STATUSES = {
  start: new Set(["started", "already_running"]),
  stop: new Set(["stopped", "not_managed", "stale_state_removed"]),
};

export function commandResultsHaveFailures(operation, results) {
  const accepted = SUCCESS_STATUSES[operation];
  if (!accepted || !Array.isArray(results)) return true;
  return results.some((item) => !accepted.has(String(item?.status || "")));
}

function commitCommandResult(operation, results) {
  const failed = commandResultsHaveFailures(operation, results);
  if (failed) process.exitCode = 1;
  return { results, failed };
}

function help() {
  console.log(`V8OS CLI

Usage:
  v8os [start]
  v8os start [--with cybercore|--all|--only engine,admin] [--mode dev|start]
  v8os preview [--rebuild|--no-build]
  v8os stop [--only engine,admin]
  v8os restart [--only engine,admin]
  v8os status [--json]
  v8os chat "message" [--session id] [--workspace path] [--safety-approval manual|reduced|minimal] [--interactive]
  v8os sessions list|show|turns|open|resume [--json]
  v8os inbox list|approve|reject|answer [--json]
  v8os workspace show|doctor|create|select|open [--json]
  v8os doctor [--json]
  v8os config list|get <domain> [--json]
  v8os config mcp list|status|install|remove [--json]
  v8os config models list|doctor|roles|recommend|set-role [--category type] [--query text] [--json]
  v8os config phone show|manifest [--json]
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
  return commitCommandResult("start", results);
}

async function runPreview(args) {
  const result = await commandPreview({
    rebuild: hasFlag(args, "--rebuild"),
    noBuild: hasFlag(args, "--no-build"),
  });
  if (hasFlag(args, "--json")) {
    printJson(result);
    return;
  }
  for (const item of result.rebuildStopResults || []) {
    if (item.status === "stopped") console.log(`${item.id}: stopped before rebuild`);
  }
  for (const item of result.buildResults || result.buildPlan) {
    if (item.status === "built") console.log(`${item.label}: production build ready. Log: ${item.logOut}`);
    else if (item.status === "already_built" || !item.shouldBuild) console.log(`${item.label}: production build already exists.`);
    else console.log(`${item.label}: production build ready.`);
  }
  renderStartResults(result.serviceResults);
  renderStartResults(result.shellResults);
  console.log("V8OS preview shell is starting. Close the window to hide it; use the tray menu to exit V8OS.");
}

async function commandStop(args) {
  const selected = hasFlag(args, "--only") ? parseComponentSelection(args) : ALL_COMPONENTS;
  const results = await stopComponents(selected);
  if (hasFlag(args, "--json")) printJson(results);
  else results.forEach((item) => console.log(`${item.id}: ${item.status}${item.reason ? ` (${item.reason})` : ""}`));
  return commitCommandResult("stop", results);
}

async function commandStatus(args) {
  const statuses = await statusComponents(ALL_COMPONENTS);
  if (hasFlag(args, "--json")) printJson(statuses);
  else renderStatus(statuses);
}

async function commandChat(args) {
  if (hasFlag(args, "--interactive") || hasFlag(args, "-i")) {
    return interactiveChat(args);
  }
  return sendChatMessage(args);
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
  if (sub === "mcp" && args[1] === "status") {
    const result = await mcpStatus();
    json ? printJson(result) : renderMcpStatus(result);
    return;
  }
  if (sub === "mcp" && args[1] === "install") {
    const result = await installMcpServer(args.slice(2));
    json ? printJson(result) : console.log(`MCP 已提交安装：${result.installed.join(", ")}`);
    return;
  }
  if (sub === "mcp" && args[1] === "remove") {
    const result = await removeMcpServer(args[2]);
    json ? printJson(result) : console.log(`MCP 已移除：${result.removed}`);
    return;
  }
  if (sub === "models" && args[1] === "doctor") {
    printJson(await modelRoleDoctor());
    return;
  }
  if (sub === "models" && args[1] === "list") {
    const result = await modelInventory({ category: optionValue(args, "--category"), query: optionValue(args, "--query"), limit: Number(optionValue(args, "--limit", "20")) });
    printJson(result);
    return;
  }
  if (sub === "models" && args[1] === "recommend") {
    const result = await recommendModel(args[2], Number(optionValue(args, "--limit", "5")));
    printJson(result);
    return;
  }
  if (sub === "models" && args[1] === "roles") {
    const result = await modelRoles();
    json ? printJson(result) : renderModelRoles(result);
    return;
  }
  if (sub === "models" && args[1] === "set-role") {
    const result = await setModelRole(args[2], args[3]);
    json ? printJson(result) : console.log(`模型角色已保存：${result.role} -> ${result.modelRef}`);
    return;
  }
  if (sub === "phone" && args[1] === "show") {
    printJson(await phonePairingSummary());
    return;
  }
  if (sub === "phone" && args[1] === "manifest") {
    const result = await phonePairingManifest();
    json ? printJson(result) : renderPhoneManifest(result);
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
  const ports = readRuntimePorts();
  const url = target === "web" ? `http://127.0.0.1:${ports.web}/chat` : `http://127.0.0.1:${ports.admin}/admin`;
  const command = process.platform === "win32" ? "cmd" : process.platform === "darwin" ? "open" : "xdg-open";
  const commandArgs = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  spawn(command, commandArgs, { detached: true, stdio: "ignore", windowsHide: true }).unref();
  console.log(url);
}

export async function main(argv) {
  const args = [...argv];
  const command = args.shift() || "start";
  if (command === "-h" || command === "--help" || command === "help") {
    help();
    return;
  }
  if (args.includes("-h") || args.includes("--help")) {
    help();
    return;
  }
  if (command === "start") return commandStart(args);
  if (command === "preview") return runPreview(args);
  if (command === "stop") return commandStop(args);
  if (command === "restart") {
    const stopped = await commandStop(args);
    if (stopped.failed) return stopped;
    return commandStart(args);
  }
  if (command === "status") return commandStatus(args);
  if (command === "chat") return commandChat(args);
  if (command === "sessions") return commandSessions(args);
  if (command === "inbox") return commandInbox(args);
  if (command === "workspace") return commandWorkspace(args);
  if (command === "doctor") return commandDoctor(args);
  if (command === "config") return commandConfig(args);
  if (command === "repair") return commandRepair(args);
  if (command === "logs") return commandLogs(args);
  if (command === "open") return commandOpen(args);
  throw new Error(`Unknown command "${command}". Run "v8os help". Repo: ${REPO_ROOT}`);
}
