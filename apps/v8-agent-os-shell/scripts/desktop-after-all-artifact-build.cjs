const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

module.exports = async function (buildResult) {
  const releaseDir = path.join(__dirname, "..", "dist", "release");
  if (!fs.existsSync(releaseDir)) return [];
  for (const entry of fs.readdirSync(releaseDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || !/win(?:-[a-z0-9]+)?-unpacked$/i.test(entry.name)) continue;
    const engineRoot = path.join(releaseDir, entry.name, "resources", "v8os", "apps", "v8-agent-os-engine");
    const pythonExe = path.join(engineRoot, ".python", "python.exe");
    const pythonZip = path.join(engineRoot, "python-runtime.zip");
    if (!fs.existsSync(pythonExe) && fs.existsSync(pythonZip)) {
      console.log(`[afterAllArtifactBuild] Unpacking python-runtime.zip in ${entry.name} for package layout verification...`);
      const tar = spawnSync("tar.exe", ["-xf", pythonZip, "-C", engineRoot]);
      if (tar.status !== 0) {
        spawnSync("powershell.exe", [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          `Microsoft.PowerShell.Archive\\Expand-Archive -LiteralPath '${pythonZip}' -DestinationPath '${engineRoot}' -Force`,
        ]);
      }
    }
  }
  return [];
};
