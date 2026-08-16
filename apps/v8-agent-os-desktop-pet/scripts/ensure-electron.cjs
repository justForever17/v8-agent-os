const path = require("path");
const { spawnSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const ensureScript = path.resolve(root, "..", "..", "scripts", "desktop", "ensure-electron-runtime.mjs");
const result = spawnSync(process.execPath, [ensureScript, "--package-root", root], {
  cwd: root,
  stdio: "inherit",
  env: process.env,
  windowsHide: true,
});

if (result.error || result.status !== 0) {
  console.error("[desktop-pet] Electron runtime acquisition failed.");
  process.exit(result.status || 1);
}
