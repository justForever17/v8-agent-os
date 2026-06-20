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
const nextBin = path.join(appDir, "node_modules", "next", "dist", "bin", "next");
if (!fs.existsSync(nextBin)) {
  throw new Error(`Next.js is not installed in ${appDir}. Run npm install first.`);
}

const managed = ensureManagedAuthSecret({
  adminDir: path.join(repoRoot, "apps", "v8-agent-os-admin"),
});
const args = [nextBin, mode];
if (mode === "dev" || mode === "start") {
  args.push("-p", port);
}

const child = spawn(process.execPath, args, {
  cwd: appDir,
  env: {
    ...process.env,
    AUTH_SECRET: managed.secret,
    NEXTAUTH_SECRET: managed.secret,
    AUTH_TRUST_HOST: "true",
    NEXTAUTH_URL: `http://127.0.0.1:${port}`,
  },
  stdio: "inherit",
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code) => {
  process.exit(code ?? 1);
});
