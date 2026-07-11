const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const electronDir = path.join(root, "node_modules", "electron");
const pathFile = path.join(electronDir, "path.txt");
const installer = path.join(electronDir, "install.js");

function electronBinaryExists() {
  if (!fs.existsSync(pathFile)) return false;
  const binaryPath = fs.readFileSync(pathFile, "utf8").trim();
  const distRoot = process.env.ELECTRON_OVERRIDE_DIST_PATH || path.join(electronDir, "dist");
  return Boolean(binaryPath) && fs.existsSync(path.join(distRoot, binaryPath));
}

if (electronBinaryExists()) {
  process.exit(0);
}

if (!fs.existsSync(installer)) {
  console.error("[desktop-pet] Electron package is missing. Run `npm install` first.");
  process.exit(1);
}

console.log("[desktop-pet] Electron binary is missing; running electron/install.js...");
const installEnv = { ...process.env };
if (!installEnv.ELECTRON_MIRROR && !installEnv.npm_config_electron_mirror) {
  installEnv.ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/";
}
const result = spawnSync(process.execPath, [installer], {
  cwd: root,
  stdio: "inherit",
  env: installEnv,
  windowsHide: true,
});

if (result.status !== 0 || !electronBinaryExists()) {
  console.error("[desktop-pet] Electron install did not produce a usable binary. Try `npm install --foreground-scripts electron`.");
  process.exit(result.status || 1);
}

console.log("[desktop-pet] Electron binary ready.");
