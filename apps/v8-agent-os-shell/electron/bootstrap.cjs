const path = require('node:path');
const { app } = require('electron');

const DESKTOP_PET_RUNTIME_MODE = 'desktop-pet';
const runtimeMode = String(process.env.V8OS_DESKTOP_RUNTIME_MODE || '').trim();
const repoRoot = process.env.V8_REPO_ROOT || (app.isPackaged
  ? path.join(process.resourcesPath, 'v8os')
  : path.resolve(__dirname, '..', '..', '..'));

process.env.V8_REPO_ROOT = repoRoot;
process.env.V8OS_SHELL_PACKAGED = app.isPackaged ? '1' : '0';
process.env.V8OS_SHELL_EXECUTABLE = process.execPath;

if (runtimeMode && runtimeMode !== DESKTOP_PET_RUNTIME_MODE) {
  throw new Error(`Unsupported V8OS desktop runtime mode: ${runtimeMode}`);
}

if (runtimeMode === DESKTOP_PET_RUNTIME_MODE) {
  const desktopPetDir = process.env.V8_DESKTOP_PET_DIR
    || path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet');
  const desktopPetMain = path.join(desktopPetDir, 'electron', 'main.cjs');
  const desktopPetUserData = path.join(app.getPath('appData'), 'V8 Agent OS Desktop Pet');

  process.env.V8_DESKTOP_PET_DIR = desktopPetDir;
  app.setName('V8 Agent OS Desktop Pet');
  app.setPath('userData', desktopPetUserData);
  app.setPath('sessionData', path.join(desktopPetUserData, 'Session Data'));
  require(desktopPetMain);
} else {
  require('./main.cjs');
}
