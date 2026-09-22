import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { commandAcp } from "./acp_commands.mjs";
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
  phoneOwner,
  phoneInitialize,
  phonePairingTicket,
  phoneDevices,
  revokePhoneDevice,
  getConfigTransaction,
  rollbackConfigTransaction,
  getNetworkConfig,
  getNetworkSchema,
  prepareNetworkConfig,
  commitNetworkConfig,
  rollbackNetworkConfig,
  getClientGatewayConfig,
  getClientGatewaySchema,
  prepareClientGatewayConfig,
  removeMcpServer,
  recommendModel,
  setModelRole,
} from "./config_commands.mjs";
import { runDoctor } from "./doctor.mjs";
import { commandInbox } from "./inbox_commands.mjs";
import { initializeServerCredentials, serverCredentialStatus } from "./credentials_commands.mjs";
import { commandPreview } from "./preview_commands.mjs";
import { runRepair } from "./repair.mjs";
import { LOG_DIR, REPO_ROOT } from "./paths.mjs";
import { startCoreComponents as startComponents, statusCoreComponents as statusComponents, stopCoreComponents as stopComponents } from "./core_control.mjs";
import { commandSessions } from "./session_commands.mjs";
import { commandServerService } from "./server_service.mjs";
import { commandWorkspace } from "./workspace_commands.mjs";
import { readRuntimePorts } from "./runtime_ports.mjs";
import {
  printJson,
  renderConfigDomains,
  renderConfigTransaction,
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
  v8os service install|upgrade --bundle <server-package-root> [--key-file <absolute-path>] [--port 9530] [--json]
  v8os service start|stop|restart|status|rollback|uninstall [--json]
  v8os packs list|install|uninstall ...
  v8os chat "message" [--session id] [--workspace path] [--safety-approval manual|reduced|minimal] [--interactive]
  v8os tui [--session id] [--screen-reader] [--no-color]
  v8os acp
  v8os sessions list|show|turns|open|resume [--json]
  v8os inbox list|approve|reject|answer [--json]
  v8os workspace show|doctor|create|select|open [--json]
  v8os doctor [--json]
  v8os config list|get <domain> [--json]
  v8os config transaction show|rollback <transaction-id> [--json]
  v8os config network show|schema|prepare|commit|rollback ... [--json]
  v8os config mcp list|status|install|remove [--json]
  v8os config models list|doctor|roles|recommend|set-role [--category type] [--query text] [--json]
  v8os config phone show|manifest|owner|init|pair|devices|revoke|schema|prepare|commit|rollback [--json]
  v8os config credentials init|status --key-file <absolute-path> [--json]
  v8os repair [--dry-run|--yes] [--json]
  v8os logs
  v8os open admin|web
`);
}

async function commandStart(args) {
  const mode = optionValue(args, "--mode", "dev");
  const selected = parseComponentSelection(args);
  const results = await startComponents(selected, { mode, lifecycle: selected.includes("shell") ? "desktop" : "daemon" });
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
  if (sub === "credentials" && ["init", "status"].includes(args[1])) {
    const keyFile = optionValue(args, "--key-file", "");
    const result = args[1] === "init" ? initializeServerCredentials(keyFile) : serverCredentialStatus(keyFile);
    printJson(result);
    return;
  }
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
  if (sub === "transaction" && (args[1] === "show" || args[1] === "rollback")) {
    const result = args[1] === "show"
      ? await getConfigTransaction(args[2])
      : await rollbackConfigTransaction(args[2]);
    json ? printJson(result) : renderConfigTransaction(result);
    if (args[1] === "rollback" && result.payload?.state && ["conflict", "recovery_required"].includes(result.payload.state)) {
      process.exitCode = 1;
    }
    return;
  }
  if (sub === "network" && args[1] === "show") {
    const result = await getNetworkConfig();
    json ? printJson(result) : renderConfigTransaction({ source: result.source, payload: { state: result.payload?.mode, target: "network", ...(result.payload?.settings || {}) } });
    return;
  }
  if (sub === "network" && args[1] === "schema") {
    const result = await getNetworkSchema();
    json ? printJson(result) : printJson(result.payload || result);
    return;
  }
  if (sub === "network" && args[1] === "prepare") {
    const raw = optionValue(args, "--settings-json");
    if (!raw) throw new Error("config network prepare requires --settings-json");
    let settings;
    try { settings = JSON.parse(raw); } catch { throw new Error("--settings-json must be valid JSON"); }
    const result = await prepareNetworkConfig(settings);
    json ? printJson(result) : renderConfigTransaction(result);
    return;
  }
  if (sub === "network" && ["commit", "rollback"].includes(args[1])) {
    const transactionId = args[2];
    const planDigest = optionValue(args.slice(3), "--plan-digest", "") || optionValue(args, "--plan-digest", "");
    const result = args[1] === "commit"
      ? await commitNetworkConfig(transactionId, planDigest)
      : await rollbackNetworkConfig(transactionId);
    json ? printJson(result) : renderConfigTransaction(result);
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
  if (sub === "phone" && args[1] === "schema") {
    const result = await getClientGatewaySchema();
    json ? printJson(result) : printJson(result.payload || result);
    return;
  }
  if (sub === "phone" && args[1] === "prepare") {
    const raw = optionValue(args, "--settings-json");
    if (!raw) throw new Error("config phone prepare requires --settings-json");
    let settings;
    try { settings = JSON.parse(raw); } catch { throw new Error("--settings-json must be valid JSON"); }
    const result = await prepareClientGatewayConfig(settings);
    json ? printJson(result) : renderConfigTransaction(result);
    return;
  }
  if (sub === "phone" && ["commit", "rollback"].includes(args[1])) {
    const transactionId = args[2];
    const planDigest = optionValue(args.slice(3), "--plan-digest", "") || optionValue(args, "--plan-digest", "");
    const result = args[1] === "commit"
      ? await commitNetworkConfig(transactionId, planDigest)
      : await rollbackNetworkConfig(transactionId);
    json ? printJson(result) : renderConfigTransaction(result);
    return;
  }
  if (sub === "phone" && args[1] === "owner") {
    printJson(await phoneOwner());
    return;
  }
  if (sub === "phone" && args[1] === "init") {
    const result = await phoneInitialize({ login: optionValue(args, "--login", "owner"), name: optionValue(args, "--name", "") });
    json ? printJson(result) : console.log(result.initialized ? "Engine owner/local session 已就绪。" : "Engine owner 尚未初始化。");
    return;
  }
  if (sub === "phone" && args[1] === "pair") {
    const result = await phonePairingTicket({
      deviceName: optionValue(args, "--device-name", ""),
      ttlMs: Number(optionValue(args, "--ttl-ms", "300000")),
      baseUrl: optionValue(args, "--base-url", ""),
    });
    json ? printJson(result) : printJson(result.payload || result);
    return;
  }
  if (sub === "phone" && args[1] === "devices") {
    const result = await phoneDevices();
    printJson(result);
    return;
  }
  if (sub === "phone" && args[1] === "revoke") {
    const result = await revokePhoneDevice(args[2]);
    json ? printJson(result) : console.log(`Phone 设备已撤销：${args[2]}`);
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

async function commandFeaturePacks(args) {
  const entry = path.join(REPO_ROOT, "scripts", "server", "feature-packs.mjs");
  if (!existsSync(entry)) throw new Error("This Engine bundle does not contain the feature-pack manager");
  const child = spawn(process.execPath, [entry, ...args], { stdio: "inherit", windowsHide: true, shell: false });
  const forwardInterrupt = () => child.kill("SIGINT");
  const forwardTerminate = () => child.kill("SIGTERM");
  process.on("SIGINT", forwardInterrupt);
  process.on("SIGTERM", forwardTerminate);
  try {
    await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => {
        process.exitCode = code ?? (signal === "SIGINT" ? 130 : signal === "SIGTERM" ? 143 : 1);
        resolve();
      });
    });
  } finally {
    process.removeListener("SIGINT", forwardInterrupt);
    process.removeListener("SIGTERM", forwardTerminate);
  }
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
  if (command === "tui") return (await import("./tui_command.mjs")).commandTui(args);
  if (command === "packs") return commandFeaturePacks(args);
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
  if (command === "service") return commandServerService(args);
  if (command === "chat") return commandChat(args);
  if (command === "acp") return commandAcp(args);
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
