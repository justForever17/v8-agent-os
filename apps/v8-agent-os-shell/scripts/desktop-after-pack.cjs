const fs = require("node:fs");
const path = require("node:path");

module.exports = async function (context) {
  if (context.electronPlatformName !== "win32") return;
  const engineRoot = path.join(context.appOutDir, "resources", "v8os", "apps", "v8-agent-os-engine");
  const pythonDir = path.join(engineRoot, ".python");
  const pythonZip = path.join(engineRoot, "python-runtime.zip");

  if (fs.existsSync(pythonZip) && fs.existsSync(pythonDir)) {
    console.log(`[afterPack] Pruning unpacked .python from ${context.appOutDir} (python-runtime.zip is bundled)`);
    fs.rmSync(pythonDir, { recursive: true, force: true });
  }
};
