import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { ALL_COMPONENTS, COMPONENTS, DEFAULT_START_COMPONENTS, parseComponentSelection } from "../src/components.mjs";
import { buildChatSubmitPayload, extractMessageText, normalizeSafetyApprovalMode } from "../src/chat_commands.mjs";
import { requireOk } from "../src/client_api.mjs";
import { buildMcpInstallPayload, extractModelRoles } from "../src/config_commands.mjs";
import { filterPendingInboxItems } from "../src/inbox_commands.mjs";
import { backupFile, readJsonFile, writeJsonFile } from "../src/json_file.mjs";
import { getPortOwners, isPortOpen } from "../src/ports.mjs";
import { verifiedComponentPortOwner } from "../src/process_manager.mjs";
import { buildLocalRepairPlan, runDoctor } from "../src/doctor.mjs";
import {
  createShellRestartLease,
  isNextBuildPresent,
  nextBuildIdPath,
  previewBuildLogPaths,
  previewRebuildStopComponentIds,
  removeOwnedShellRestartLease,
} from "../src/preview_commands.mjs";
import { currentWorkspaceBinding, currentWorkspacePath, inspectWorkspace, resolveWorkspacePath } from "../src/workspace_commands.mjs";
import { main as runCli } from "../src/cli.mjs";

const currentFile = fileURLToPath(import.meta.url);
const cliRoot = path.resolve(path.dirname(currentFile), "..");
const repoRoot = path.resolve(cliRoot, "..", "..");

function runWindowsCommand(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: options.timeoutMs || 10000,
  });
}

function powershellLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

test("default start components exclude CyberCore", () => {
  assert.deepEqual(DEFAULT_START_COMPONENTS, ["engine", "admin", "web"]);
  assert.deepEqual(parseComponentSelection([]), ["engine", "admin", "web"]);
  assert.equal(DEFAULT_START_COMPONENTS.includes("shell"), false);
  assert.equal(DEFAULT_START_COMPONENTS.includes("desktop-pet"), false);
});

test("--with adds optional components without replacing defaults", () => {
  assert.deepEqual(parseComponentSelection(["--with", "cybercore"]), ["engine", "admin", "web", "cybercore"]);
});

test("--only narrows component set", () => {
  assert.deepEqual(parseComponentSelection(["--only", "engine,admin"]), ["engine", "admin"]);
});

test("--all returns all components", () => {
  assert.deepEqual(parseComponentSelection(["--all"]), ALL_COMPONENTS);
  assert.ok(ALL_COMPONENTS.includes("shell"));
  assert.ok(ALL_COMPONENTS.includes("desktop-pet"));
});

test("subcommand help is read-only and does not execute preview", async () => {
  const lines = [];
  const originalLog = console.log;
  console.log = (...args) => lines.push(args.join(" "));
  try {
    await runCli(["preview", "--help"]);
  } finally {
    console.log = originalLog;
  }
  assert.match(lines.join("\n"), /v8os preview \[--rebuild\|--no-build\]/);
});

test("local repair plan proposes safe state root creation", () => {
  const plan = buildLocalRepairPlan([{ id: "state_root", status: "warning" }]);
  assert.equal(plan.actions[0].id, "create_state_root");
  assert.equal(plan.actions[0].safe, true);
});

test("local repair plan treats runtime installation as explicit action", () => {
  const plan = buildLocalRepairPlan([{ id: "python", status: "warning" }]);
  assert.equal(plan.actions[0].id, "install_python");
  assert.equal(plan.actions[0].safe, false);
});

test("local repair plan can safely refresh missing admin auth secret", () => {
  const plan = buildLocalRepairPlan([{ id: "admin_auth_secret", status: "warning", path: "secret" }]);
  assert.equal(plan.actions[0].id, "refresh_admin_auth_secret");
  assert.equal(plan.actions[0].safe, true);
});

test("port probe detects a listening local port", async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  try {
    assert.equal(await isPortOpen(address.port), true);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("port owner probe is safe for invalid or unopened ports", () => {
  assert.deepEqual(getPortOwners(0), []);
});

test("json backup writes timestamped backup without changing source", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-json-"));
  const file = path.join(dir, "config.json");
  writeJsonFile(file, { ok: true });
  const backup = backupFile(file, "unit");
  assert.deepEqual(readJsonFile(file), { ok: true });
  assert.deepEqual(readJsonFile(backup), { ok: true });
  assert.match(path.basename(backup), /^config\.json\.unit\./);
});

test("admin and web commands use managed auth launcher", () => {
  const admin = COMPONENTS.admin.command({ mode: "dev" });
  const web = COMPONENTS.web.command({ mode: "start" });
  assert.equal(admin.command, process.execPath);
  assert.equal(web.command, process.execPath);
  assert.ok(admin.args.some((part) => part.endsWith("run-next-with-managed-auth.mjs")));
  assert.ok(web.args.some((part) => part.endsWith("run-next-with-managed-auth.mjs")));
  assert.ok(admin.args.includes("admin"));
  assert.ok(web.args.includes("web"));
  assert.ok(admin.args.includes("9528"));
  assert.ok(web.args.includes("9527"));
});

test("preview shell and desktop pet are no-port managed components", () => {
  assert.equal(COMPONENTS.shell.port, null);
  assert.equal(COMPONENTS["desktop-pet"].port, null);
  const shell = COMPONENTS.shell.command();
  const pet = COMPONENTS["desktop-pet"].command();
  assert.equal(shell.command, process.execPath);
  assert.equal(pet.command, process.execPath);
  assert.ok(shell.args.some((part) => part.endsWith("launch-shell.mjs")));
  assert.ok(pet.args.some((part) => part.endsWith("launch-desktop-pet.mjs")));
  assert.equal(pet.env.V8_DESKTOP_PET_MANAGED_BY_SHELL, "1");
});

test("desktop pet managed mode suppresses its own tray", () => {
  const main = fs.readFileSync(path.join(repoRoot, "apps", "v8-agent-os-desktop-pet", "electron", "main.cjs"), "utf8");
  assert.match(main, /V8_DESKTOP_PET_MANAGED_BY_SHELL/);
  assert.match(main, /MANAGED_BY_SHELL/);
  assert.match(main, /if \(!MANAGED_BY_SHELL\) createTray\(\)/);
});

test("desktop pet survives Shell replacement through detached handoff and exact Shell termination", () => {
  const launcher = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-shell", "scripts", "launch-desktop-pet.mjs"),
    "utf8",
  );
  const interposer = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-shell", "scripts", "spawn-detached-electron.mjs"),
    "utf8",
  );
  const processManager = fs.readFileSync(
    path.join(repoRoot, "apps", "v8-agent-os-cli", "src", "process_manager.mjs"),
    "utf8",
  );
  assert.match(launcher, /launchDetachedElectron/);
  assert.match(interposer, /detached:\s*true/);
  assert.match(interposer, /child\.unref\(\)/);
  assert.match(processManager, /desktop-pet\.json/);
  assert.match(processManager, /effectiveManagedPid/);
  assert.match(processManager, /shell-control\.json/);
  assert.match(processManager, /killPid\(pid, \{ tree: id !== "shell" \}\)/);
  assert.match(processManager, /stopped_during_kill/);
  assert.match(processManager, /spawnSync\("taskkill", args, \{ encoding: "utf8", windowsHide: true \}\)/);
});

test("preview build check is based on Next BUILD_ID", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-preview-build-"));
  assert.equal(isNextBuildPresent(dir), false);
  fs.mkdirSync(path.dirname(nextBuildIdPath(dir)), { recursive: true });
  fs.writeFileSync(nextBuildIdPath(dir), "unit-build\n", "utf8");
  assert.equal(isNextBuildPresent(dir), true);
});

test("preview build logs are written to CLI logs", () => {
  const logs = previewBuildLogPaths({ app: "web" });
  assert.match(logs.out, /web\.build\.out\.log$/);
  assert.match(logs.err, /web\.build\.err\.log$/);
});

test("preview rebuild restarts shell, Next servers, and Engine before verification", () => {
  assert.deepEqual(previewRebuildStopComponentIds({ rebuild: true }), ["shell", "admin", "web", "engine"]);
  assert.deepEqual(previewRebuildStopComponentIds({ rebuild: false }), []);
  const previewSource = fs.readFileSync(path.join(cliRoot, "src", "preview_commands.mjs"), "utf8");
  assert.match(previewSource, /stopVerifiedPortOwners:\s*\["engine"\]/);
});

test("preview rebuild adopts only a verified current-repo Engine port owner", () => {
  const engineDir = COMPONENTS.engine.cwd;
  const verified = verifiedComponentPortOwner("engine", {
    pid: 41000,
    parentPid: 40999,
    executablePath: "D:\\Program Files\\python\\python.exe",
    commandLine: '"D:\\Program Files\\python\\python.exe" -m uvicorn main:app --port 9530',
    parentExecutablePath: path.join(engineDir, ".venv", "Scripts", "python.exe"),
    parentCommandLine: `"${path.join(engineDir, ".venv", "Scripts", "python.exe")}" -m uvicorn main:app --port 9530`,
  });
  const unrelated = verifiedComponentPortOwner("engine", {
    pid: 42000,
    parentPid: 0,
    executablePath: "C:\\Python\\python.exe",
    commandLine: 'python -m http.server 9530',
  });

  assert.deepEqual(verified, {
    ownerPid: 41000,
    killPid: 40999,
    matchedBy: "verified_parent_runtime",
  });
  assert.equal(unrelated, null);
  assert.equal(verifiedComponentPortOwner("admin", { pid: 41000 }), null);
});

test("preview rebuild lease is atomic, bounded, and removable only by its owner", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-shell-restart-lease-"));
  const filePath = path.join(root, "shell-restart.json");
  try {
    const lease = createShellRestartLease({ filePath, now: 1_000, ttlMs: 30_000 });
    const persisted = JSON.parse(fs.readFileSync(filePath, "utf8"));
    assert.equal(persisted.id, lease.id);
    assert.equal(persisted.reason, "preview_rebuild");
    assert.equal(persisted.expiresAt, 31_000);
    assert.equal(removeOwnedShellRestartLease({ ...lease, id: "not-owner" }), false);
    assert.equal(fs.existsSync(filePath), true);
    assert.equal(removeOwnedShellRestartLease(lease), true);
    assert.equal(fs.existsSync(filePath), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Windows root wrappers reach the CLI help without entering the app directory", { skip: process.platform !== "win32" }, () => {
  const cmdPath = path.join(repoRoot, "v8os.cmd");
  const ps1Path = path.join(repoRoot, "v8os.ps1");

  const cmd = runWindowsCommand("powershell", [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    `& ${powershellLiteral(cmdPath)} --help`,
  ]);
  assert.equal(cmd.status, 0, cmd.stderr || cmd.stdout);
  assert.match(cmd.stdout, /v8os start/);
  assert.match(cmd.stdout, /v8os config phone/);

  const ps1 = runWindowsCommand("powershell", [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    ps1Path,
    "--help",
  ]);
  assert.equal(ps1.status, 0, ps1.stderr || ps1.stdout);
  assert.match(ps1.stdout, /v8os start/);
  assert.match(ps1.stdout, /v8os config phone/);
});

test("Windows PATH helper defaults to a non-mutating dry run", { skip: process.platform !== "win32" }, () => {
  const helperPath = path.join(repoRoot, "scripts", "install-v8os-path.ps1");
  const result = runWindowsCommand("powershell", [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    helperPath,
  ]);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.match(result.stdout, /V8OS PATH helper|already on the user PATH/);
  if (!/already on the user PATH/.test(result.stdout)) {
    assert.match(result.stdout, /Dry run only/);
    assert.match(result.stdout, /-Apply/);
  }
});

test("doctor fallback returns local checks without requiring engine", async () => {
  const result = await runDoctor({ preferEngine: false });
  assert.equal(result.source, "local_fallback");
  assert.ok(result.summary.total >= 4);
  assert.ok(result.checks.some((check) => check.id === "config.json"));
});

test("mcp install payload builds stdio config", () => {
  const payload = buildMcpInstallPayload([
    "sqlite",
    "--type",
    "stdio",
    "--command",
    "npx",
    "--arg",
    "-y",
    "--arg",
    "@modelcontextprotocol/server-sqlite",
    "--env",
    "FOO=bar",
  ]);
  assert.deepEqual(payload, {
    mcpServers: {
      sqlite: {
        type: "stdio",
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-sqlite"],
        env: { FOO: "bar" },
      },
    },
  });
});

test("mcp install payload builds http config", () => {
  const payload = buildMcpInstallPayload([
    "remote",
    "--type",
    "http",
    "--url",
    "http://127.0.0.1:3000/mcp",
    "--header",
    "Authorization=Bearer token",
  ]);
  assert.equal(payload.mcpServers.remote.type, "http");
  assert.equal(payload.mcpServers.remote.url, "http://127.0.0.1:3000/mcp");
  assert.equal(payload.mcpServers.remote.headers.Authorization, "Bearer token");
});

test("model role extractor accepts registry payload shape", () => {
  assert.deepEqual(extractModelRoles({ payload: { data: { roles: { default: "provider:model" } } } }), {
    default: "provider:model",
  });
});

test("chat payload preserves session, workspace, project, and spec mode", () => {
  const payload = buildChatSubmitPayload({
    sessionId: "session-a",
    message: "你好",
    workspacePath: "E:/Projects/demo",
    workspaceId: "demo-workspace",
    projectId: "demo",
    specMode: true,
    safetyApprovalMode: "minimal",
  });
  assert.equal(payload.session_id, "session-a");
  assert.equal(payload.conversationId, "session-a");
  assert.equal(payload.messages[0].content, "你好");
  assert.equal(payload.data.workspacePath, "E:/Projects/demo");
  assert.equal(payload.data.workspaceId, "demo-workspace");
  assert.equal(payload.data.projectId, "demo");
  assert.equal(payload.data.specMode, true);
  assert.equal(payload.data.safetyApprovalMode, "minimal");
});

test("chat safety approval mode defaults to reduced for local trusted clients", () => {
  assert.equal(normalizeSafetyApprovalMode("manual"), "manual");
  assert.equal(normalizeSafetyApprovalMode("reduced"), "reduced");
  assert.equal(normalizeSafetyApprovalMode("minimal"), "minimal");
  assert.equal(normalizeSafetyApprovalMode("unknown"), "reduced");
  assert.equal(buildChatSubmitPayload({
    sessionId: "session-a",
    message: "你好",
  }).data.safetyApprovalMode, "reduced");
});

test("client API errors surface workspace trust guidance", () => {
  assert.throws(
    () => requireOk({
      ok: false,
      status: 400,
      data: { detail: { error: "workspace_side_effect_blocked", summary: "先选择并信任项目工作区" } },
    }, "提交消息"),
    /v8os workspace create <path> --select/,
  );
});

test("chat text extractor handles string and rich parts", () => {
  assert.equal(extractMessageText({ content: "plain" }), "plain");
  assert.equal(extractMessageText({ content: [{ text: "a" }, { content: "b" }] }), "a\nb");
  assert.equal(extractMessageText({ parts: ["x", { text: "y" }] }), "x\ny");
});

test("inbox pending filter excludes closed and empty items", () => {
  assert.deepEqual(filterPendingInboxItems([
    { id: "a", status: "pending" },
    { id: "b", status: "resolved" },
    { id: "", status: "pending" },
    { id: "c", status: "waiting" },
  ]).map((item) => item.id), ["a", "c"]);
});

test("workspace helpers resolve and inspect local directories without touching config", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v8os-cli-workspace-"));
  fs.mkdirSync(path.join(dir, ".agents", "rules"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".agents", "rules", "AGENTS.md"), "# Rules\n", "utf8");
  assert.equal(resolveWorkspacePath(dir), path.resolve(dir));
  assert.equal(currentWorkspacePath({ workspace: { agent_workspace_path: dir } }), path.resolve(dir));
  assert.deepEqual(currentWorkspaceBinding({
    workspace: {
      agent_workspace_path: dir,
      projectId: "project-demo",
      workspaceId: "workspace-demo",
      workspaceTrustState: "trusted",
      workspaceTrustSource: "cli_user_confirmed",
    },
  }), {
    path: path.resolve(dir),
    projectId: "project-demo",
    workspaceId: "workspace-demo",
    workspaceTrustState: "trusted",
    workspaceTrustSource: "cli_user_confirmed",
  });
  const report = inspectWorkspace(dir);
  assert.equal(report.path, path.resolve(dir));
  assert.ok(report.checks.some((check) => check.id === "agents_rules" && check.status === "ok"));
});
