import { spawn } from "node:child_process";
import fs from "node:fs";

import { electronCliPath, paths } from "./electron-launcher.mjs";

const [, , target, handoffPath] = process.argv;
if (!target || !handoffPath) {
  throw new Error("Detached Electron launch requires a target and handoff path.");
}

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(process.execPath, [electronCliPath(), target], {
  cwd: paths.repoRoot,
  stdio: "inherit",
  env,
  detached: true,
  windowsHide: true,
});
child.unref();
fs.writeFileSync(handoffPath, JSON.stringify({ pid: child.pid }), "utf8");
