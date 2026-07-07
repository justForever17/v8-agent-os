import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  ADMIN_DIR,
  CONFIG_PATH,
  CYBERCORE_DIR,
  DEFAULT_PORTS,
  DESKTOP_PET_DIR,
  ENGINE_DIR,
  MCP_CONFIG_PATH,
  STATE_ROOT,
  WEB_DIR,
} from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { getPortOwners, isPortOpen } from "./ports.mjs";
import { readJsonFile } from "./json_file.mjs";

function commandVersion(command, args = ["--version"]) {
  const result = spawnSync(command, args, { encoding: "utf8", timeout: 2500 });
  if (result.error) {
    const shellResult = spawnSync([command, ...args].join(" "), { encoding: "utf8", timeout: 2500, shell: true });
    return {
      ok: shellResult.status === 0,
      value: (shellResult.stdout || shellResult.stderr || "").trim().split(/\r?\n/)[0] || "",
    };
  }
  return {
    ok: result.status === 0,
    value: (result.stdout || result.stderr || "").trim().split(/\r?\n/)[0] || "",
  };
}

function checkJsonFile(filePath, label) {
  if (!fs.existsSync(filePath)) {
    return { id: label, status: "warning", summary: `${label} 不存在`, path: filePath };
  }
  try {
    readJsonFile(filePath, {});
    return { id: label, status: "ok", summary: `${label} 可读取`, path: filePath };
  } catch (error) {
    return { id: label, status: "failed", summary: `${label} 不是有效 JSON`, path: filePath, message: error.message };
  }
}

function checkPathExists(id, label, targetPath, missingStatus = "failed") {
  return {
    id,
    status: fs.existsSync(targetPath) ? "ok" : missingStatus,
    summary: `${label} ${fs.existsSync(targetPath) ? "存在" : "缺失"}`,
    path: targetPath,
  };
}

function checkNodeAppDependencies(id, label, appDir, requiredRelativePaths = []) {
  const packageJson = path.join(appDir, "package.json");
  if (!fs.existsSync(packageJson)) {
    return { id, status: "warning", summary: `${label} 未找到 package.json`, path: packageJson };
  }
  const nodeModules = path.join(appDir, "node_modules");
  if (!fs.existsSync(nodeModules)) {
    return { id, status: "warning", summary: `${label} 依赖未安装`, path: nodeModules };
  }
  const missing = requiredRelativePaths
    .map((relativePath) => path.join(appDir, relativePath))
    .filter((targetPath) => !fs.existsSync(targetPath));
  if (missing.length) {
    return {
      id,
      status: "warning",
      summary: `${label} 依赖不完整`,
      path: nodeModules,
      missing,
    };
  }
  return { id, status: "ok", summary: `${label} 依赖可用`, path: nodeModules };
}

function checkEngineVenv() {
  const pythonCandidate = process.platform === "win32"
    ? path.join(ENGINE_DIR, ".venv", "Scripts", "python.exe")
    : path.join(ENGINE_DIR, ".venv", "bin", "python");
  if (!fs.existsSync(pythonCandidate)) {
    return { id: "engine_venv", status: "warning", summary: "Engine Python venv 缺失", path: pythonCandidate };
  }
  const python = commandVersion(pythonCandidate, ["--version"]);
  const pip = commandVersion(pythonCandidate, ["-m", "pip", "--version"]);
  return {
    id: "engine_venv",
    status: python.ok && pip.ok ? "ok" : "warning",
    summary: `Engine venv ${python.value || "Python 不可用"}; pip ${pip.value || "不可用"}`,
    path: pythonCandidate,
  };
}

function checkAdminAuthSecret() {
  const secretFile = path.join(STATE_ROOT, "secrets", "admin-auth-secret");
  return {
    id: "admin_auth_secret",
    status: fs.existsSync(secretFile) ? "ok" : "warning",
    summary: fs.existsSync(secretFile) ? "Admin auth secret 已存在" : "Admin auth secret 缺失，可安全生成",
    path: secretFile,
  };
}

function checkModelRoles() {
  try {
    const config = readJsonFile(CONFIG_PATH, {});
    const roles = config?.models?.roles || {};
    const roleNames = Object.keys(roles).sort();
    const important = ["default", "supervisor", "subagent"];
    const missing = important.filter((role) => !roles[role]);
    return {
      id: "model_roles",
      status: missing.length ? "warning" : "ok",
      summary: missing.length
        ? `模型角色缺少 ${missing.join(", ")}`
        : `模型角色已配置 ${roleNames.length} 个`,
      roles,
      missing,
    };
  } catch (error) {
    return { id: "model_roles", status: "warning", summary: "无法读取模型角色配置", message: error.message };
  }
}

function checkPhoneConnectionManifest() {
  try {
    const config = readJsonFile(CONFIG_PATH, {});
    const systemBase = config?.systemBase || config?.["system-base"] || {};
    const manifest = systemBase.remoteLinkManifest || systemBase.pairingManifest || null;
    const remoteLink = systemBase.remoteLink || null;
    const urls = [
      ...(Array.isArray(manifest?.adminUrls) ? manifest.adminUrls : []),
      ...(Array.isArray(remoteLink?.adminUrls) ? remoteLink.adminUrls : []),
      remoteLink?.adminUrl,
      remoteLink?.manualUrl,
    ].filter(Boolean);
    return {
      id: "phone_connection_manifest",
      status: urls.length ? "ok" : "warning",
      summary: urls.length ? `Phone 可用连接地址候选 ${urls.length} 个` : "没有可用的 Phone 连接地址候选",
      urls,
    };
  } catch (error) {
    return { id: "phone_connection_manifest", status: "warning", summary: "无法读取 Phone 连接地址配置", message: error.message };
  }
}

function electronBinaryPath(appDir) {
  if (process.platform === "win32") return path.join(appDir, "node_modules", "electron", "dist", "electron.exe");
  if (process.platform === "darwin") return path.join(appDir, "node_modules", "electron", "dist", "Electron.app", "Contents", "MacOS", "Electron");
  return path.join(appDir, "node_modules", "electron", "dist", "electron");
}

function checkElectronInstall(id, label, appDir) {
  const binary = electronBinaryPath(appDir);
  return {
    id,
    status: fs.existsSync(binary) ? "ok" : "warning",
    summary: fs.existsSync(binary) ? `${label} Electron 可用` : `${label} Electron 二进制缺失`,
    path: binary,
  };
}

export async function runDoctor({ preferEngine = true } = {}) {
  if (preferEngine) {
    try {
      const response = await fetchJson(`http://127.0.0.1:${DEFAULT_PORTS.engine}/v1/system/doctor`, { timeoutMs: 3500 });
      if (response.ok && response.data) {
        return { source: "engine", ...response.data };
      }
    } catch {
      // Fall back to local checks below.
    }
  }

  const checks = [];
  checks.push({ id: "state_root", status: fs.existsSync(STATE_ROOT) ? "ok" : "warning", summary: `数据目录 ${fs.existsSync(STATE_ROOT) ? "存在" : "不存在"}`, path: STATE_ROOT });
  checks.push(checkJsonFile(CONFIG_PATH, "config.json"));
  checks.push(checkJsonFile(MCP_CONFIG_PATH, "mcp.json"));
  checks.push(checkPathExists("engine_dir", "Engine 源码目录", ENGINE_DIR));
  checks.push(checkPathExists("admin_dir", "Admin 源码目录", ADMIN_DIR));
  checks.push(checkPathExists("web_dir", "Web 源码目录", WEB_DIR));
  checks.push(checkPathExists("desktop_pet_dir", "桌宠源码目录", DESKTOP_PET_DIR, "warning"));
  checks.push(checkPathExists("cybercore_dir", "CyberCore 源码目录", CYBERCORE_DIR, "warning"));

  for (const [id, port] of Object.entries(DEFAULT_PORTS)) {
    const open = await isPortOpen(port);
    checks.push({
      id: `${id}_port`,
      status: open ? "ok" : "warning",
      summary: `${id} 端口 ${port}${open ? " 已监听" : " 未监听"}`,
      port,
      owners: open ? getPortOwners(port) : [],
    });
  }

  const node = commandVersion(process.execPath, ["--version"]);
  checks.push({ id: "node", status: node.ok ? "ok" : "failed", summary: `Node ${node.value || "不可用"}` });
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const npm = commandVersion(npmCommand, ["--version"]);
  checks.push({ id: "npm", status: npm.ok ? "ok" : "warning", summary: `npm ${npm.value || "不可用"}` });
  const pythonCandidate = process.platform === "win32" ? `${ENGINE_DIR}\\.venv\\Scripts\\python.exe` : `${ENGINE_DIR}/.venv/bin/python`;
  const python = fs.existsSync(pythonCandidate) ? commandVersion(pythonCandidate, ["--version"]) : commandVersion("python", ["--version"]);
  checks.push({ id: "python", status: python.ok ? "ok" : "warning", summary: `Python ${python.value || "不可用"}` });
  checks.push(checkEngineVenv());
  checks.push(checkNodeAppDependencies("admin_dependencies", "Admin", ADMIN_DIR, ["node_modules/next/dist/bin/next"]));
  checks.push(checkNodeAppDependencies("web_dependencies", "Web", WEB_DIR, ["node_modules/next/dist/bin/next"]));
  checks.push(checkNodeAppDependencies("desktop_pet_dependencies", "桌宠", DESKTOP_PET_DIR));
  checks.push(checkElectronInstall("desktop_pet_electron", "桌宠", DESKTOP_PET_DIR));
  checks.push(checkNodeAppDependencies("cybercore_dependencies", "CyberCore", CYBERCORE_DIR));
  checks.push(checkAdminAuthSecret());
  checks.push(checkModelRoles());
  checks.push(checkPhoneConnectionManifest());

  let ok = 0;
  let warning = 0;
  let failed = 0;
  for (const check of checks) {
    if (check.status === "ok") ok += 1;
    else if (check.status === "failed") failed += 1;
    else warning += 1;
  }
  return {
    source: "local_fallback",
    summary: { ok, warning, failed, total: checks.length },
    checks,
    repairPlan: buildLocalRepairPlan(checks),
  };
}

export function buildLocalRepairPlan(checks) {
  const actions = [];
  for (const check of checks || []) {
    if (check.id === "state_root" && check.status !== "ok") {
      actions.push({ id: "create_state_root", title: "创建 V8OS 数据目录", safe: true });
    }
    if ((check.id === "config.json" || check.id === "mcp.json") && check.status === "failed") {
      actions.push({ id: `backup_${check.id}`, title: `备份并提示修复 ${check.id}`, safe: true, path: check.path });
    }
    if (check.id?.endsWith("_port") && check.status === "ok") {
      actions.push({ id: `inspect_${check.id}`, title: `检查端口 ${check.port} 当前占用者`, safe: true, owners: check.owners || [] });
    }
    if (check.id === "admin_auth_secret" && check.status !== "ok") {
      actions.push({ id: "refresh_admin_auth_secret", title: "生成缺失的 Admin auth secret", safe: true, path: check.path });
    }
    if ((check.id === "npm" || check.id === "python" || check.id === "engine_venv") && check.status !== "ok") {
      actions.push({ id: `install_${check.id}`, title: `安装或修复 ${check.id} 运行时`, safe: false });
    }
    if (check.id?.endsWith("_dependencies") && check.status !== "ok") {
      actions.push({ id: `install_${check.id}`, title: `安装 ${check.id.replace("_dependencies", "")} 依赖`, safe: false, path: check.path });
    }
    if (check.id?.endsWith("_electron") && check.status !== "ok") {
      actions.push({ id: `repair_${check.id}`, title: `修复 ${check.id.replace("_electron", "")} Electron 安装`, safe: false, path: check.path });
    }
  }
  return { actions };
}
