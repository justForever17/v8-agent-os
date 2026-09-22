const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const { recordDesktopFault } = require('../lib/desktop-fault-log.cjs');
const {
  createGpuRecoveryController,
  softwareRenderingRelaunchArgs,
  softwareRenderingRequested,
} = require('../lib/gpu-recovery.cjs');

const softwareRendering = softwareRenderingRequested(process.argv, process.env);
const repoRoot = process.env.V8_REPO_ROOT || (app.isPackaged
  ? path.join(process.resourcesPath, 'v8os')
  : path.resolve(__dirname, '..', '..', '..'));


function isolatedUserDataRoot() {
  const configured = String(process.env.V8OS_DESKTOP_ISOLATED_USER_DATA_ROOT || '').trim();
  if (!configured) return '';
  const governedStateRoot = String(process.env.V8_AGENT_OS_HOME || '').trim();
  if (!governedStateRoot) {
    throw new Error('V8OS_DESKTOP_ISOLATED_USER_DATA_ROOT requires V8_AGENT_OS_HOME.');
  }
  const resolvedStateRoot = path.resolve(governedStateRoot);
  const resolvedUserDataRoot = path.resolve(configured);
  const relative = path.relative(resolvedStateRoot, resolvedUserDataRoot);
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('Isolated Electron user data must stay inside V8_AGENT_OS_HOME.');
  }
  return resolvedUserDataRoot;
}

const isolatedRoot = isolatedUserDataRoot();
if (isolatedRoot) {
  const modeDir = path.join(isolatedRoot, 'shell');
  const sessionDataDir = path.join(modeDir, 'Session Data');
  fs.mkdirSync(sessionDataDir, { recursive: true, mode: 0o700 });
  app.setPath('userData', modeDir);
  app.setPath('sessionData', sessionDataDir);
}

// Electron 36+ selects GTK4 on GNOME. The packaged Linux surface and native
// accessibility stack are GTK3-based; keep the choice explicit unless a
// host/operator has deliberately supplied another gtk-version switch.
if (process.platform === 'linux' && !app.commandLine.hasSwitch('gtk-version')) {
  app.commandLine.appendSwitch('gtk-version', '3');
}

process.env.V8_REPO_ROOT = repoRoot;
process.env.V8OS_SHELL_PACKAGED = app.isPackaged ? '1' : '0';
process.env.V8OS_SHELL_EXECUTABLE = process.execPath;
process.env.V8OS_ELECTRON_NO_SANDBOX = app.commandLine.hasSwitch('no-sandbox') ? '1' : '0';
process.env.V8OS_SOFTWARE_RENDERING = softwareRendering ? '1' : '0';

if (softwareRendering) app.disableHardwareAcceleration();

const gpuRecovery = createGpuRecoveryController({
  softwareRendering,
  logger: console,
  onRecover() {
    recordDesktopFault('surface-recovery', { stage: 'relaunch-requested' });
    const accepted = app.emit(
      'v8os-gpu-recovery-requested',
      softwareRenderingRelaunchArgs(process.argv),
    );
    if (!accepted) {
      recordDesktopFault('surface-recovery', { stage: 'relaunch-unhandled' });
      console.error('[V8OS Desktop] GPU recovery has no governed shutdown handler.');
    }
  },
});
app.on('child-process-gone', (_event, details) => {
  if (details?.type === 'GPU') recordDesktopFault('gpu-process-exited', details);
  gpuRecovery.handle(details);
});
app.on('before-quit', () => gpuRecovery.disable());
app.on('v8os-governed-shutdown-started', () => gpuRecovery.disable());

require('./main.cjs');
