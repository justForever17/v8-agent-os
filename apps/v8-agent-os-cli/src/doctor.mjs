import fs from "node:fs";
import { spawnSync } from "node:child_process";
import { ADMIN_DIR, CONFIG_PATH, DEFAULT_PORTS, ENGINE_DIR, MCP_CONFIG_PATH, STATE_ROOT, WEB_DIR } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { isPortOpen } from "./ports.mjs";
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
  checks.push({ id: "engine_dir", status: fs.existsSync(ENGINE_DIR) ? "ok" : "failed", summary: "Engine 源码目录", path: ENGINE_DIR });
  checks.push({ id: "admin_dir", status: fs.existsSync(ADMIN_DIR) ? "ok" : "failed", summary: "Admin 源码目录", path: ADMIN_DIR });
  checks.push({ id: "web_dir", status: fs.existsSync(WEB_DIR) ? "ok" : "failed", summary: "Web 源码目录", path: WEB_DIR });

  for (const [id, port] of Object.entries(DEFAULT_PORTS)) {
    checks.push({ id: `${id}_port`, status: await isPortOpen(port) ? "ok" : "warning", summary: `${id} 端口 ${port}`, port });
  }

  const node = commandVersion(process.execPath, ["--version"]);
  checks.push({ id: "node", status: node.ok ? "ok" : "failed", summary: `Node ${node.value || "不可用"}` });
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const npm = commandVersion(npmCommand, ["--version"]);
  checks.push({ id: "npm", status: npm.ok ? "ok" : "warning", summary: `npm ${npm.value || "不可用"}` });
  const pythonCandidate = process.platform === "win32" ? `${ENGINE_DIR}\\.venv\\Scripts\\python.exe` : `${ENGINE_DIR}/.venv/bin/python`;
  const python = fs.existsSync(pythonCandidate) ? commandVersion(pythonCandidate, ["--version"]) : commandVersion("python", ["--version"]);
  checks.push({ id: "python", status: python.ok ? "ok" : "warning", summary: `Python ${python.value || "不可用"}` });

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
      actions.push({ id: `inspect_${check.id}`, title: `检查端口 ${check.port} 当前占用者`, safe: true });
    }
    if ((check.id === "npm" || check.id === "python") && check.status !== "ok") {
      actions.push({ id: `install_${check.id}`, title: `安装或修复 ${check.id} 运行时`, safe: false });
    }
  }
  return { actions };
}
