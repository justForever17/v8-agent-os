import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { ADMIN_DIR, CYBERCORE_DIR, DEFAULT_PORTS, DESKTOP_PET_DIR, ENGINE_DIR, LOG_DIR, REPO_ROOT, SHELL_DIR, WEB_DIR } from "./paths.mjs";

function enginePlaywrightEnv() {
  const browsersPath = path.join(ENGINE_DIR, ".playwright-browsers");
  return fs.existsSync(browsersPath) ? { PLAYWRIGHT_BROWSERS_PATH: browsersPath } : {};
}

function enginePython() {
  if (process.env.V8_ENGINE_PYTHON) return process.env.V8_ENGINE_PYTHON;
  const candidates = process.platform === "win32"
    ? [
        path.join(ENGINE_DIR, ".python", "pythonw.exe"),
        path.join(ENGINE_DIR, ".python", "python.exe"),
        path.join(ENGINE_DIR, ".venv", "Scripts", "pythonw.exe"),
        path.join(ENGINE_DIR, ".venv", "Scripts", "python.exe"),
      ]
    : [
        path.join(ENGINE_DIR, ".python", "bin", "python3"),
        path.join(ENGINE_DIR, ".python", "bin", "python"),
        path.join(ENGINE_DIR, ".venv", "bin", "python3"),
        path.join(ENGINE_DIR, ".venv", "bin", "python"),
      ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || (process.platform === "win32" ? "python" : "python3");
}

function nodeRuntime(extraEnv = {}) {
  const env = { ...extraEnv };
  if (process.versions?.electron) {
    env.ELECTRON_RUN_AS_NODE = "1";
  }
  return {
    command: process.execPath,
    env,
  };
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
          PYTHONIOENCODING: "utf-8",
          PYTHONUTF8: "1",
          ...enginePlaywrightEnv(),
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
      const runtime = nodeRuntime();
      return {
        command: runtime.command,
        args: ["scripts/run-next-with-managed-auth.mjs", "--app", "admin", "--mode", mode, "--port", String(DEFAULT_PORTS.admin)],
        cwd: REPO_ROOT,
        env: runtime.env,
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
      const runtime = nodeRuntime();
      return {
        command: runtime.command,
        args: ["scripts/run-next-with-managed-auth.mjs", "--app", "web", "--mode", mode, "--port", String(DEFAULT_PORTS.web)],
        cwd: REPO_ROOT,
        env: runtime.env,
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
  "desktop-pet": {
    id: "desktop-pet",
    label: "Desktop Pet",
    port: null,
    detachedHandoff: true,
    cwd: REPO_ROOT,
    command() {
      const runtime = nodeRuntime({
        V8_DESKTOP_PET_MANAGED_BY_SHELL: "1",
        V8_ADMIN_BASE_URL: `http://127.0.0.1:${DEFAULT_PORTS.admin}`,
        V8_WEB_BASE_URL: `http://127.0.0.1:${DEFAULT_PORTS.web}`,
        V8_REPO_ROOT: REPO_ROOT,
        V8_DESKTOP_PET_DIR: DESKTOP_PET_DIR,
      });
      return {
        command: runtime.command,
        args: ["apps/v8-agent-os-shell/scripts/launch-desktop-pet.mjs"],
        cwd: REPO_ROOT,
        env: runtime.env,
      };
    },
  },
  shell: {
    id: "shell",
    label: "V8OS Shell",
    port: null,
    cwd: SHELL_DIR,
    command() {
      const runtime = nodeRuntime({
        V8_ADMIN_BASE_URL: `http://127.0.0.1:${DEFAULT_PORTS.admin}`,
        V8_WEB_BASE_URL: `http://127.0.0.1:${DEFAULT_PORTS.web}`,
        V8_ENGINE_BASE_URL: `http://127.0.0.1:${DEFAULT_PORTS.engine}`,
        V8_REPO_ROOT: REPO_ROOT,
        V8_DESKTOP_PET_DIR: DESKTOP_PET_DIR,
      });
      return {
        command: runtime.command,
        args: ["apps/v8-agent-os-shell/scripts/launch-shell.mjs"],
        cwd: REPO_ROOT,
        env: runtime.env,
      };
    },
  },
};

export const DEFAULT_START_COMPONENTS = ["engine", "admin", "web"];
export const ALL_COMPONENTS = ["engine", "admin", "web", "cybercore", "desktop-pet", "shell"];

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
