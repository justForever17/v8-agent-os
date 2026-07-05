import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { ADMIN_DIR, CYBERCORE_DIR, DEFAULT_PORTS, ENGINE_DIR, LOG_DIR, REPO_ROOT, WEB_DIR } from "./paths.mjs";

function enginePython() {
  const candidate = process.platform === "win32"
    ? path.join(ENGINE_DIR, ".venv", "Scripts", "python.exe")
    : path.join(ENGINE_DIR, ".venv", "bin", "python");
  return fs.existsSync(candidate) ? candidate : "python";
}

export const COMPONENTS = {
  engine: {
    id: "engine",
    label: "Engine",
    port: DEFAULT_PORTS.engine,
    cwd: ENGINE_DIR,
    healthUrl: `http://127.0.0.1:${DEFAULT_PORTS.engine}/health`,
    command() {
      return {
        command: enginePython(),
        args: ["main.py"],
        cwd: ENGINE_DIR,
        env: {
          ENGINE_HOST: "127.0.0.1",
          ENGINE_PORT: String(DEFAULT_PORTS.engine),
        },
      };
    },
  },
  admin: {
    id: "admin",
    label: "Admin",
    port: DEFAULT_PORTS.admin,
    cwd: REPO_ROOT,
    healthUrl: `http://127.0.0.1:${DEFAULT_PORTS.admin}/admin`,
    command(options = {}) {
      const mode = options.mode || "dev";
      return {
        command: process.execPath,
        args: ["scripts/run-next-with-managed-auth.mjs", "--app", "admin", "--mode", mode, "--port", String(DEFAULT_PORTS.admin)],
        cwd: REPO_ROOT,
        env: {},
      };
    },
  },
  web: {
    id: "web",
    label: "Web",
    port: DEFAULT_PORTS.web,
    cwd: REPO_ROOT,
    healthUrl: `http://127.0.0.1:${DEFAULT_PORTS.web}/chat`,
    command(options = {}) {
      const mode = options.mode || "dev";
      return {
        command: process.execPath,
        args: ["scripts/run-next-with-managed-auth.mjs", "--app", "web", "--mode", mode, "--port", String(DEFAULT_PORTS.web)],
        cwd: REPO_ROOT,
        env: {},
      };
    },
  },
  cybercore: {
    id: "cybercore",
    label: "CyberCore",
    port: DEFAULT_PORTS.cybercore,
    cwd: CYBERCORE_DIR,
    healthUrl: `http://127.0.0.1:${DEFAULT_PORTS.cybercore}/health`,
    command(options = {}) {
      const mode = options.mode || "dev";
      const script = mode === "start" ? "start" : "dev";
      if (process.platform === "win32") {
        return {
          command: "cmd",
          args: ["/c", "npm", "run", script],
          cwd: CYBERCORE_DIR,
          env: {},
        };
      }
      return {
        command: "npm",
        args: ["run", script],
        cwd: CYBERCORE_DIR,
        env: {},
      };
    },
  },
};

export const DEFAULT_START_COMPONENTS = ["engine", "admin", "web"];
export const ALL_COMPONENTS = ["engine", "admin", "web", "cybercore"];

export function parseComponentSelection(args) {
  if (args.includes("--all")) return ALL_COMPONENTS;
  const withIndex = args.indexOf("--with");
  if (withIndex >= 0) {
    const raw = String(args[withIndex + 1] || "");
    const extras = raw.split(",").map((item) => item.trim()).filter(Boolean);
    return [...new Set([...DEFAULT_START_COMPONENTS, ...extras])].filter((id) => COMPONENTS[id]);
  }
  const onlyIndex = args.indexOf("--only");
  if (onlyIndex >= 0) {
    return String(args[onlyIndex + 1] || "")
      .split(",")
      .map((item) => item.trim())
      .filter((id) => COMPONENTS[id]);
  }
  return DEFAULT_START_COMPONENTS;
}

export function logPathsFor(componentId) {
  return {
    out: path.join(LOG_DIR, `${componentId}.out.log`),
    err: path.join(LOG_DIR, `${componentId}.err.log`),
  };
}
