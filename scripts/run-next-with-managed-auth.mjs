import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ensureManagedAuthSecret } from "./ensure-admin-auth-secret.mjs";

function argumentValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || "").trim() : fallback;
}

const repoRoot = path.resolve(import.meta.dirname, "..");

export function findStandaloneServer(appDir, app) {
  const standaloneRoot = path.join(appDir, ".next", "standalone");
  const candidates = [
    path.join(standaloneRoot, "apps", `v8-agent-os-${app}`, "server.js"),
    path.join(standaloneRoot, "server.js"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function repairWindowsDirectorySymlinks(root) {
  if (process.platform !== "win32" || !fs.existsSync(root)) return 0;
  const stack = [root];
  let repaired = 0;
  while (stack.length > 0) {
    const current = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(candidate);
        continue;
      }
      if (!entry.isSymbolicLink()) continue;
      try {
        const target = path.resolve(path.dirname(candidate), fs.readlinkSync(candidate));
        if (!fs.statSync(target).isDirectory() || fs.existsSync(candidate)) continue;
        fs.rmSync(candidate, { force: true });
        fs.symlinkSync(target, candidate, "junction");
        repaired += 1;
      } catch {
        // Leave unrelated or externally-managed links untouched. Node will
        // report a precise module error if one of those links is unusable.
      }
    }
  }
  return repaired;
}

function standaloneAssetPairs(appDir, serverPath) {
  const standaloneAppRoot = path.dirname(serverPath);
  return [
    [path.join(appDir, ".next", "static"), path.join(standaloneAppRoot, ".next", "static")],
    [path.join(appDir, "public"), path.join(standaloneAppRoot, "public")],
  ];
}

export function stageStandaloneAssets(appDir, serverPath) {
  if (!serverPath) throw new Error("Cannot stage assets without a standalone server.");
  const standaloneAppRoot = path.dirname(serverPath);
  const repairedLinks = repairWindowsDirectorySymlinks(path.join(standaloneAppRoot, "node_modules"));
  if (repairedLinks > 0) {
    console.log(`[V8OS] Repaired ${repairedLinks} Windows standalone directory link(s).`);
  }
  for (const [source, target] of standaloneAssetPairs(appDir, serverPath)) {
    if (!fs.existsSync(source)) throw new Error(`Standalone asset source is missing: ${source}`);
    fs.rmSync(target, { recursive: true, force: true });
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.cpSync(source, target, { recursive: true });
  }
}

export function assertStandaloneAssetsReady(appDir, serverPath) {
  if (!serverPath) throw new Error("Standalone server is missing. Rebuild the production bundle before starting it.");
  const missing = standaloneAssetPairs(appDir, serverPath)
    .flatMap(([source, target]) => [source, target])
    .filter((candidate) => !fs.existsSync(candidate));
  if (missing.length > 0) {
    throw new Error(`Standalone assets are incomplete. Rebuild before starting; missing: ${missing.join(", ")}`);
  }
}

function main(args = process.argv.slice(2)) {
  const app = argumentValue(args, "--app");
  const mode = argumentValue(args, "--mode");
  const port = argumentValue(args, "--port", app === "admin" ? "9528" : "9527");
  if (!['admin', 'web'].includes(app) || !['dev', 'build', 'start'].includes(mode)) {
    throw new Error("Usage: --app admin|web --mode dev|build|start [--port 9528]");
  }
  // Phone is the only remote client and reaches Engine exclusively through the
  // authenticated Admin BFF. Web remains a local-only desktop surface.
  // Binding Admin to the IPv6 unspecified address keeps the Phone gateway dual-stack.
  const runtimeHostname = app === "admin"
    ? (String(process.env.V8_ADMIN_HOSTNAME || "").trim() || "::")
    : "127.0.0.1";
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

  const standaloneServer = mode === "start" ? findStandaloneServer(appDir, app) : "";
  if (mode === "start") assertStandaloneAssetsReady(appDir, standaloneServer);
  const nextBin = path.join(appDir, "node_modules", "next", "dist", "bin", "next");
  if (!standaloneServer && !fs.existsSync(nextBin)) {
    throw new Error(`Next.js is not installed in ${appDir}, and no standalone server was found. Run npm install or build standalone first.`);
  }

  const managed = ensureManagedAuthSecret({
    adminDir: path.join(repoRoot, "apps", "v8-agent-os-admin"),
  });
  const childArgs = standaloneServer ? [standaloneServer] : [nextBin, mode];
  if (!standaloneServer && mode === "build") childArgs.push("--webpack");
  if (!standaloneServer && (mode === "dev" || mode === "start")) childArgs.push("-p", port);
  const childCwd = standaloneServer ? path.dirname(standaloneServer) : appDir;
  const baseNodeOptions = process.env.NODE_OPTIONS || "";
  const buildNodeOptions = baseNodeOptions.includes("--max-old-space-size")
    ? baseNodeOptions
    : `${baseNodeOptions} --max-old-space-size=8192`.trim();

  const child = spawn(process.execPath, childArgs, {
    cwd: childCwd,
    env: {
      ...process.env,
      AUTH_SECRET: managed.secret,
      NEXTAUTH_SECRET: managed.secret,
      AUTH_TRUST_HOST: "true",
      NEXTAUTH_URL: `http://127.0.0.1:${port}`,
      HOSTNAME: runtimeHostname,
      PORT: port,
      V8_AGENT_OS_HOME: mode === "build" ? buildHome : process.env.V8_AGENT_OS_HOME,
      V8_NEXT_BUILD: mode === "build" ? "1" : process.env.V8_NEXT_BUILD,
      USERPROFILE: mode === "build" ? buildHome : process.env.USERPROFILE,
      HOME: mode === "build" ? buildHome : process.env.HOME,
      APPDATA: mode === "build" ? buildAppData : process.env.APPDATA,
      LOCALAPPDATA: mode === "build" ? buildLocalAppData : process.env.LOCALAPPDATA,
      V8_AGENT_OS_REPO_ROOT: repoRoot,
      V8_ENGINE_DIR: path.join(repoRoot, "apps", "v8-agent-os-engine"),
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
    let exitCode = code ?? 1;
    if (exitCode === 0 && mode === "build") {
      try {
        const builtStandaloneServer = findStandaloneServer(appDir, app);
        stageStandaloneAssets(appDir, builtStandaloneServer);
        assertStandaloneAssetsReady(appDir, builtStandaloneServer);
        console.log(`[V8OS] Staged ${app} standalone static and public assets.`);
      } catch (error) {
        console.error(`[V8OS] Failed to stage ${app} standalone assets: ${error instanceof Error ? error.message : String(error)}`);
        exitCode = 1;
      }
    }
    process.exit(exitCode);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
