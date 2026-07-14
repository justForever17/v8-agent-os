import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";

import { ADMIN_DIR, DEFAULT_PORTS, LOG_DIR, REPO_ROOT, STATE_ROOT, WEB_DIR } from "./paths.mjs";
import { startComponents, stopComponents } from "./process_manager.mjs";

export const PREVIEW_REBUILD_STOP_COMPONENTS = ["shell", "admin", "web", "engine"];
export const SHELL_RESTART_LEASE_PATH = path.join(STATE_ROOT, "runtime", "shell-restart.json");

export const PREVIEW_NEXT_APPS = {
  admin: {
    app: "admin",
    label: "Admin",
    dir: ADMIN_DIR,
    port: DEFAULT_PORTS.admin,
  },
  web: {
    app: "web",
    label: "Web",
    dir: WEB_DIR,
    port: DEFAULT_PORTS.web,
  },
};

export function nextBuildIdPath(appDir) {
  return path.join(appDir, ".next", "BUILD_ID");
}

export function isNextBuildPresent(appDir) {
  return fs.existsSync(nextBuildIdPath(appDir));
}

export function previewBuildLogPaths(app) {
  return {
    out: path.join(LOG_DIR, `${app.app}.build.out.log`),
    err: path.join(LOG_DIR, `${app.app}.build.err.log`),
  };
}

export function planPreviewBuilds(options = {}) {
  const rebuild = Boolean(options.rebuild);
  return Object.values(PREVIEW_NEXT_APPS).map((app) => ({
    app: app.app,
    label: app.label,
    dir: app.dir,
    built: isNextBuildPresent(app.dir),
    shouldBuild: rebuild || !isNextBuildPresent(app.dir),
  }));
}

export function previewRebuildStopComponentIds(options = {}) {
  return options.rebuild ? [...PREVIEW_REBUILD_STOP_COMPONENTS] : [];
}

export function createShellRestartLease(options = {}) {
  const filePath = options.filePath || SHELL_RESTART_LEASE_PATH;
  const now = Number(options.now) || Date.now();
  const lease = {
    version: 1,
    id: crypto.randomUUID(),
    reason: "preview_rebuild",
    ownerPid: process.pid,
    createdAt: new Date(now).toISOString(),
    expiresAt: now + Math.max(30_000, Number(options.ttlMs) || 600_000),
  };
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(lease, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  try {
    fs.renameSync(temporaryPath, filePath);
  } catch {
    fs.rmSync(filePath, { force: true });
    fs.renameSync(temporaryPath, filePath);
  }
  return { ...lease, filePath };
}

export function removeOwnedShellRestartLease(lease) {
  if (!lease?.filePath || !lease?.id) return false;
  try {
    const current = JSON.parse(fs.readFileSync(lease.filePath, "utf8"));
    if (current?.id !== lease.id) return false;
    fs.rmSync(lease.filePath, { force: true });
    return true;
  } catch {
    return false;
  }
}

async function waitForShellControlDescriptor(timeoutMs = 10_000) {
  const descriptorPath = path.join(STATE_ROOT, "runtime", "shell-control.json");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(descriptorPath)) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return false;
}

export function runNextBuild(app) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  const logs = previewBuildLogPaths(app);
  const out = fs.openSync(logs.out, "a");
  const err = fs.openSync(logs.err, "a");
  const result = spawnSync(process.execPath, [
    "scripts/run-next-with-managed-auth.mjs",
    "--app",
    app.app,
    "--mode",
    "build",
    "--port",
    String(app.port),
  ], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    stdio: ["ignore", out, err],
    windowsHide: true,
  });
  fs.closeSync(out);
  fs.closeSync(err);
  if (result.status !== 0) {
    throw new Error(`${app.label} production build failed. See ${logs.err}`);
  }
  return logs;
}

export async function commandPreview(args = {}) {
  const rebuild = Boolean(args.rebuild);
  const noBuild = Boolean(args.noBuild);
  const buildPlan = planPreviewBuilds({ rebuild });
  const missing = buildPlan.filter((item) => !item.built);
  if (noBuild && missing.length) {
    throw new Error(`Preview build is missing for: ${missing.map((item) => item.label).join(", ")}. Run v8os preview without --no-build or use --rebuild.`);
  }
  const rebuildStopComponentIds = previewRebuildStopComponentIds({ rebuild });
  const shellRestartLease = rebuildStopComponentIds.includes("shell")
    ? createShellRestartLease()
    : null;
  try {
    const rebuildStopResults = rebuildStopComponentIds.length > 0
      ? stopComponents(rebuildStopComponentIds)
      : [];
    const stopFailures = rebuildStopResults.filter((item) => item.status === "stop_failed");
    if (stopFailures.length > 0) {
      throw new Error(`Unable to stop the running preview before rebuild: ${stopFailures.map((item) => item.id).join(", ")}. Run v8os stop and retry.`);
    }
    const buildResults = [];
    for (const item of buildPlan) {
      if (!item.shouldBuild) {
        buildResults.push({ ...item, status: "already_built" });
        continue;
      }
      const logs = runNextBuild(PREVIEW_NEXT_APPS[item.app]);
      buildResults.push({ ...item, status: "built", logOut: logs.out, logErr: logs.err });
    }
    const serviceResults = await startComponents(["engine", "admin", "web"], { mode: "start" });
    const shellResults = await startComponents(["shell"], { mode: "start" });
    if (shellRestartLease) await waitForShellControlDescriptor();
    return {
      buildPlan,
      rebuildStopResults,
      buildResults,
      serviceResults,
      shellResults,
    };
  } finally {
    if (shellRestartLease) removeOwnedShellRestartLease(shellRestartLease);
  }
}
