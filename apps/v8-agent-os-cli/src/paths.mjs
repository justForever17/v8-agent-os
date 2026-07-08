import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const cliRoot = path.resolve(path.dirname(currentFile), "..");
const defaultRepoRoot = path.resolve(cliRoot, "..", "..");

export const REPO_ROOT = path.resolve(process.env.V8_REPO_ROOT || defaultRepoRoot);
export const STATE_ROOT = path.resolve(
  process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os"),
);
export const RUNTIME_DIR = path.join(STATE_ROOT, "runtime", "cli");
export const REPORTS_DIR = path.join(STATE_ROOT, "reports", "cli_base");
export const LOG_DIR = path.join(STATE_ROOT, "logs", "cli");
export const PROCESS_STATE_PATH = path.join(RUNTIME_DIR, "processes.json");
export const CONFIG_PATH = path.join(STATE_ROOT, "config.json");
export const MCP_CONFIG_PATH = path.join(STATE_ROOT, "mcp.json");

export const ENGINE_DIR = path.resolve(process.env.V8_ENGINE_DIR || path.join(REPO_ROOT, "apps", "v8-agent-os-engine"));
export const ADMIN_DIR = path.resolve(process.env.V8_ADMIN_DIR || path.join(REPO_ROOT, "apps", "v8-agent-os-admin"));
export const WEB_DIR = path.resolve(process.env.V8_WEB_DIR || path.join(REPO_ROOT, "apps", "v8-agent-os-web"));
export const DESKTOP_PET_DIR = path.resolve(process.env.V8_DESKTOP_PET_DIR || path.join(REPO_ROOT, "apps", "v8-agent-os-desktop-pet"));
export const SHELL_DIR = path.resolve(process.env.V8_SHELL_DIR || path.join(REPO_ROOT, "apps", "v8-agent-os-shell"));
export const CYBERCORE_DIR = path.resolve(REPO_ROOT, "..", "out", "CyberCore");

export const DEFAULT_PORTS = {
  engine: 9530,
  admin: 9528,
  web: 9527,
  cybercore: 8787,
};

export function timestampForFile(date = new Date()) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "_");
}
