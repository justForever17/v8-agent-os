import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { ensureManagedAuthSecret } from "./ensure-admin-auth-secret.mjs";

function argumentValue(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || "").trim() : fallback;
}

const repoRoot = path.resolve(import.meta.dirname, "..");
const app = argumentValue("--app");
const mode = argumentValue("--mode");
const port = argumentValue("--port", app === "admin" ? "9528" : "9527");
if (!['admin', 'web'].includes(app) || !['dev', 'build', 'start'].includes(mode)) {
  throw new Error("Usage: --app admin|web --mode dev|build|start [--port 9528]");
}

const appDir = path.join(repoRoot, "apps", `v8-agent-os-${app}`);
const buildHome = path.join(repoRoot, ".next-v8os-home");
const buildAppData = path.join(buildHome, "AppData", "Roaming");
const buildLocalAppData = path.join(buildHome, "AppData", "Local");
if (mode === "build") {
  fs.mkdirSync(buildHome, { recursive: true });
  fs.mkdirSync(buildAppData, { recursive: true });
  fs.mkdirSync(buildLocalAppData, { recursive: true });
  process.env.V8_AGENT_OS_HOME = buildHome;
  process.env.V8_NEXT_BUILD = "1";
  process.env.USERPROFILE = buildHome;
  process.env.HOME = buildHome;
  process.env.APPDATA = buildAppData;
  process.env.LOCALAPPDATA = buildLocalAppData;
}
function findStandaloneServer() {
  const standaloneRoot = path.join(appDir, ".next", "standalone");
  const candidates = [
    path.join(standaloneRoot, "apps", `v8-agent-os-${app}`, "server.js"),
    path.join(standaloneRoot, "server.js"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function stageStandaloneAssets(serverPath) {
  if (!serverPath) return;
  const standaloneAppRoot = path.dirname(serverPath);
  const assets = [
    [path.join(appDir, ".next", "static"), path.join(standaloneAppRoot, ".next", "static")],
    [path.join(appDir, "public"), path.join(standaloneAppRoot, "public")],
  ];
  for (const [source, target] of assets) {
    if (!fs.existsSync(source)) continue;
    fs.rmSync(target, { recursive: true, force: true });
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.cpSync(source, target, { recursive: true });
  }
}

const standaloneServer = mode === "start" ? findStandaloneServer() : "";
stageStandaloneAssets(standaloneServer);
const nextBin = path.join(appDir, "node_modules", "next", "dist", "bin", "next");
if (!standaloneServer && !fs.existsSync(nextBin)) {
  throw new Error(`Next.js is not installed in ${appDir}, and no standalone server was found. Run npm install or build standalone first.`);
}

const managed = ensureManagedAuthSecret({
  adminDir: path.join(repoRoot, "apps", "v8-agent-os-admin"),
});
const args = standaloneServer
  ? [standaloneServer]
  : [nextBin, mode];
if (!standaloneServer && mode === "build") {
  args.push("--webpack");
}
if (!standaloneServer && (mode === "dev" || mode === "start")) {
  args.push("-p", port);
}
const childCwd = standaloneServer ? path.dirname(standaloneServer) : appDir;
const baseNodeOptions = process.env.NODE_OPTIONS || "";
const buildNodeOptions = baseNodeOptions.includes("--max-old-space-size")
  ? baseNodeOptions
  : `${baseNodeOptions} --max-old-space-size=8192`.trim();

const child = spawn(process.execPath, args, {
  cwd: childCwd,
  env: {
    ...process.env,
    AUTH_SECRET: managed.secret,
    NEXTAUTH_SECRET: managed.secret,
    AUTH_TRUST_HOST: "true",
    NEXTAUTH_URL: `http://127.0.0.1:${port}`,
    HOSTNAME: "127.0.0.1",
    PORT: port,
    V8_AGENT_OS_HOME: mode === "build" ? buildHome : process.env.V8_AGENT_OS_HOME,
    V8_NEXT_BUILD: mode === "build" ? "1" : process.env.V8_NEXT_BUILD,
    USERPROFILE: mode === "build" ? buildHome : process.env.USERPROFILE,
    HOME: mode === "build" ? buildHome : process.env.HOME,
    APPDATA: mode === "build" ? buildAppData : process.env.APPDATA,
    LOCALAPPDATA: mode === "build" ? buildLocalAppData : process.env.LOCALAPPDATA,
    NODE_OPTIONS: mode === "build" ? buildNodeOptions : process.env.NODE_OPTIONS,
    NEXT_TELEMETRY_DISABLED: "1",
  },
  stdio: "inherit",
  windowsHide: true,
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code) => {
  process.exit(code ?? 1);
});
