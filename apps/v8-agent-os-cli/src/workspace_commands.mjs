import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { CONFIG_PATH, STATE_ROOT } from "./paths.mjs";
import { adminJson, requireOk } from "./client_api.mjs";
import { backupFile, ensureDir, readJsonFile, writeJsonFile } from "./json_file.mjs";

function hasFlag(args, flag) {
  return args.includes(flag);
}

function expandHome(input) {
  const text = String(input || "").trim();
  if (!text) return "";
  if (text === "~") return os.homedir();
  if (text.startsWith("~/") || text.startsWith("~\\")) return path.join(os.homedir(), text.slice(2));
  return text;
}

export function resolveWorkspacePath(input = "") {
  const expanded = expandHome(input);
  if (!expanded) return path.join(STATE_ROOT, "workspace");
  return path.resolve(expanded);
}

export function currentWorkspacePath(config = readJsonFile(CONFIG_PATH, {})) {
  return resolveWorkspacePath(
    config?.workspace?.agent_workspace_path
      || config?.workspace?.path
      || config?.workspacePath
      || path.join(STATE_ROOT, "workspace"),
  );
}

export function currentWorkspaceBinding(config = readJsonFile(CONFIG_PATH, {})) {
  const workspace = config?.workspace || {};
  return {
    path: currentWorkspacePath(config),
    projectId: String(workspace.project_id || workspace.projectId || config?.projectId || "").trim(),
    workspaceId: String(workspace.workspace_id || workspace.workspaceId || "").trim(),
    workspaceTrustState: String(workspace.workspace_trust_state || workspace.workspaceTrustState || "").trim(),
    workspaceTrustSource: String(workspace.workspace_trust_source || workspace.workspaceTrustSource || "").trim(),
  };
}

export function inspectWorkspace(workspacePath) {
  const target = resolveWorkspacePath(workspacePath);
  const exists = fs.existsSync(target);
  const stat = exists ? fs.statSync(target) : null;
  const agentsRules = path.join(target, ".agents", "rules", "AGENTS.md");
  const specDir = path.join(target, ".v8", "specs");
  const gitDir = path.join(target, ".git");
  const checks = [
    { id: "path", status: path.isAbsolute(target) ? "ok" : "failed", summary: target },
    { id: "exists", status: exists ? "ok" : "warning", summary: exists ? "目录存在" : "目录不存在" },
    { id: "directory", status: stat?.isDirectory() ? "ok" : exists ? "failed" : "warning", summary: stat?.isDirectory() ? "可作为工作区" : "不是目录" },
    { id: "agents_rules", status: fs.existsSync(agentsRules) ? "ok" : "warning", summary: fs.existsSync(agentsRules) ? "存在工作区规则" : "未发现 .agents/rules/AGENTS.md" },
    { id: "specs", status: fs.existsSync(specDir) ? "ok" : "info", summary: fs.existsSync(specDir) ? "存在 Spec 目录" : "未发现 Spec 目录" },
    { id: "git", status: fs.existsSync(gitDir) ? "ok" : "info", summary: fs.existsSync(gitDir) ? "Git 仓库" : "非 Git 仓库或未初始化" },
  ];
  return { path: target, checks };
}

function workspaceProjectName(workspacePath) {
  const base = path.basename(resolveWorkspacePath(workspacePath)).trim();
  return base || "V8OS Workspace";
}

export async function registerTrustedWorkspaceProject(workspacePath, { projectId = "", workspaceId = "", required = false } = {}) {
  const target = resolveWorkspacePath(workspacePath);
  try {
    const project = requireOk(await adminJson("/api/client/projects", {
      method: "POST",
      body: {
        ...(projectId ? { id: projectId } : {}),
        name: workspaceProjectName(target),
        ...(workspaceId ? { workspaceId } : {}),
        workspacePath: target,
        workspaceTrustState: "trusted",
        workspaceTrustSource: "cli_user_confirmed",
      },
      timeoutMs: 10_000,
    }), "确认工作区信任");
    return {
      registered: true,
      path: String(project.workspacePath || target),
      projectId: String(project.id || project.projectId || projectId || "").trim(),
      workspaceId: String(project.workspaceId || workspaceId || "").trim(),
      workspaceTrustState: String(project.workspaceTrustState || "trusted"),
      workspaceTrustSource: String(project.workspaceTrustSource || "cli_user_confirmed"),
    };
  } catch (error) {
    if (required) {
      throw new Error(
        `无法确认工作区信任：${error instanceof Error ? error.message : String(error)}。` +
        "请确认 v8os preview/start 已启动，或先在 Admin/Web 中选择并信任项目工作区。",
      );
    }
    return {
      registered: false,
      path: target,
      projectId,
      workspaceId,
      workspaceTrustState: "",
      workspaceTrustSource: "",
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}

function renderWorkspaceInspection(result) {
  console.log(`Workspace: ${result.path}`);
  for (const check of result.checks) {
    console.log(`- [${String(check.status).toUpperCase()}] ${check.id}: ${check.summary}`);
  }
}

export function createWorkspace(targetPath, { select = false } = {}) {
  const target = resolveWorkspacePath(targetPath);
  ensureDir(target);
  ensureDir(path.join(target, ".agents", "rules"));
  ensureDir(path.join(target, ".v8", "specs"));
  const rulesPath = path.join(target, ".agents", "rules", "AGENTS.md");
  if (!fs.existsSync(rulesPath)) {
    fs.writeFileSync(rulesPath, "# Workspace Rules\n\n本文件用于记录当前工作区的协作规则。\n", "utf8");
  }
  const result = { path: target, selected: false };
  if (select) {
    selectWorkspace(target);
    result.selected = true;
  }
  return result;
}

export function selectWorkspace(targetPath, binding = {}) {
  const target = resolveWorkspacePath(targetPath);
  if (!fs.existsSync(target) || !fs.statSync(target).isDirectory()) {
    throw new Error(`工作区不存在或不是目录：${target}`);
  }
  const config = readJsonFile(CONFIG_PATH, {});
  if (fs.existsSync(CONFIG_PATH)) backupFile(CONFIG_PATH, "workspace-select");
  config.workspace = {
    ...(config.workspace || {}),
    agent_workspace_path: target,
    path: target,
    ...(binding.projectId ? { projectId: binding.projectId, project_id: binding.projectId } : {}),
    ...(binding.workspaceId ? { workspaceId: binding.workspaceId, workspace_id: binding.workspaceId } : {}),
    ...(binding.workspaceTrustState ? { workspaceTrustState: binding.workspaceTrustState, workspace_trust_state: binding.workspaceTrustState } : {}),
    ...(binding.workspaceTrustSource ? { workspaceTrustSource: binding.workspaceTrustSource, workspace_trust_source: binding.workspaceTrustSource } : {}),
  };
  writeJsonFile(CONFIG_PATH, config);
  return { path: target, configPath: CONFIG_PATH };
}

function openPath(targetPath) {
  const target = resolveWorkspacePath(targetPath || currentWorkspacePath());
  const command = process.platform === "win32" ? "cmd" : process.platform === "darwin" ? "open" : "xdg-open";
  const commandArgs = process.platform === "win32" ? ["/c", "start", "", target] : [target];
  spawn(command, commandArgs, { detached: true, stdio: "ignore" }).unref();
  return target;
}

export async function commandWorkspace(args) {
  const sub = args[0] || "show";
  const json = hasFlag(args, "--json");
  if (sub === "show") {
    const result = { path: currentWorkspacePath(), configPath: CONFIG_PATH };
    json ? console.log(JSON.stringify(result, null, 2)) : console.log(`Workspace: ${result.path}`);
    return result;
  }
  if (sub === "doctor") {
    const target = args[1] && !args[1].startsWith("--") ? args[1] : currentWorkspacePath();
    const result = inspectWorkspace(target);
    json ? console.log(JSON.stringify(result, null, 2)) : renderWorkspaceInspection(result);
    return result;
  }
  if (sub === "create") {
    const target = args[1];
    if (!target) throw new Error("workspace create requires <path>");
    const result = createWorkspace(target, { select: false });
    const trust = await registerTrustedWorkspaceProject(result.path, { required: false });
    if (hasFlag(args, "--select")) {
      selectWorkspace(result.path, trust.registered ? trust : {});
      result.selected = true;
    }
    result.trust = trust;
    json ? console.log(JSON.stringify(result, null, 2)) : console.log(`已创建工作区：${result.path}${result.selected ? "（已选择）" : ""}`);
    return result;
  }
  if (sub === "select") {
    const target = args[1];
    if (!target) throw new Error("workspace select requires <path>");
    const resolved = resolveWorkspacePath(target);
    const trust = await registerTrustedWorkspaceProject(resolved, { required: false });
    const result = selectWorkspace(resolved, trust.registered ? trust : {});
    result.trust = trust;
    json ? console.log(JSON.stringify(result, null, 2)) : console.log(`已选择工作区：${result.path}`);
    return result;
  }
  if (sub === "open") {
    const target = openPath(args[1] || currentWorkspacePath());
    console.log(target);
    return { path: target };
  }
  throw new Error(`Unknown workspace command: ${args.join(" ")}`);
}
