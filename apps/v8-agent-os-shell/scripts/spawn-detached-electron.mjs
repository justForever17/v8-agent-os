import { spawn } from "node:child_process";
import fs from "node:fs";

import { desktopRuntimeSpawnSpec } from "./electron-launcher.mjs";

const [, , target, handoffPath] = process.argv;
if (!target || !handoffPath) {
  throw new Error("Detached Electron launch requires a target and handoff path.");
}

const spec = desktopRuntimeSpawnSpec(target);
const child = spawn(spec.command, spec.args, {
  cwd: spec.cwd,
  stdio: "inherit",
  env: spec.env,
  detached: true,
  windowsHide: true,
});

await new Promise((resolve, reject) => {
  child.once("error", reject);
  child.once("spawn", resolve);
});
child.unref();
fs.writeFileSync(handoffPath, JSON.stringify({ pid: child.pid }), "utf8");
