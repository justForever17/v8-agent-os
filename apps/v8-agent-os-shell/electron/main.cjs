const { app, BrowserWindow, Menu, Tray, dialog, nativeImage, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { buildTrayMenuModel } = require('../lib/tray-menu.cjs');

const repoRoot = process.env.V8_REPO_ROOT || path.resolve(__dirname, '..', '..', '..');
const desktopPetDir = process.env.V8_DESKTOP_PET_DIR || path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet');
const webBaseUrl = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
const adminBaseUrl = process.env.V8_ADMIN_BASE_URL || 'http://127.0.0.1:9528';
const processStatePath = path.join(process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os'), 'runtime', 'cli', 'processes.json');

let mainWindow = null;
let tray = null;
let quitting = false;
let desktopPetRunning = false;

function v8osCommand(args, options = {}) {
  if (process.platform === 'win32') {
    return spawnSync('cmd', ['/c', path.join(repoRoot, 'v8os.cmd'), ...args], {
      cwd: repoRoot,
      encoding: 'utf8',
      timeout: options.timeout || 15000,
    });
  }
  return spawnSync('node', [path.join(repoRoot, 'apps', 'v8-agent-os-cli', 'bin', 'v8os.mjs'), ...args], {
    cwd: repoRoot,
    encoding: 'utf8',
    timeout: options.timeout || 15000,
  });
}

function v8osCommandAsync(args) {
  const child = process.platform === 'win32'
    ? spawn('cmd', ['/c', path.join(repoRoot, 'v8os.cmd'), ...args], { cwd: repoRoot, windowsHide: true })
    : spawn('node', [path.join(repoRoot, 'apps', 'v8-agent-os-cli', 'bin', 'v8os.mjs'), ...args], { cwd: repoRoot });
  child.on('exit', () => {
    refreshStatus();
  });
  return child;
}

function shellIcon() {
  const petIcon = path.join(desktopPetDir, 'electron', 'assets', 'tray-icon.png');
  if (fs.existsSync(petIcon)) {
    const image = nativeImage.createFromPath(petIcon);
    if (!image.isEmpty()) return image;
  }
  return nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAF/wJ+gZtC6QAAAABJRU5ErkJggg==');
}

function showMainWindow() {
  if (!mainWindow) return;
  mainWindow.show();
  mainWindow.focus();
}

function openAdmin() {
  shell.openExternal(`${adminBaseUrl}/admin`);
}

function refreshStatus() {
  const result = v8osCommand(['status', '--json'], { timeout: 8000 });
  if (result.status === 0) {
    try {
      const statuses = JSON.parse(result.stdout);
      desktopPetRunning = statuses.some((item) => item.id === 'desktop-pet' && item.state === 'managed_running');
    } catch {
      desktopPetRunning = false;
    }
  }
  updateTrayMenu();
}

function showServiceStatus() {
  const result = v8osCommand(['status', '--json'], { timeout: 8000 });
  const message = result.status === 0 ? result.stdout : (result.stderr || result.stdout || '无法读取服务状态。');
  dialog.showMessageBox({
    type: result.status === 0 ? 'info' : 'warning',
    title: 'V8OS 服务状态',
    message: '当前服务状态',
    detail: message,
  });
}

function toggleDesktopPet() {
  if (desktopPetRunning) {
    v8osCommandAsync(['stop', '--only', 'desktop-pet']);
    return;
  }
  v8osCommandAsync(['start', '--only', 'desktop-pet', '--mode', 'start']);
}

function quitV8OS() {
  quitting = true;
  v8osCommand(['stop', '--only', 'engine,admin,web,desktop-pet'], { timeout: 20000 });
  try {
    const state = JSON.parse(fs.readFileSync(processStatePath, 'utf8'));
    if (state && state.processes) {
      delete state.processes.shell;
      fs.writeFileSync(processStatePath, `${JSON.stringify(state, null, 2)}\n`, 'utf8');
    }
  } catch {}
  app.quit();
}

function updateTrayMenu() {
  if (!tray) return;
  const model = buildTrayMenuModel({ desktopPetRunning });
  const template = model.map((item) => {
    if (item.type === 'separator') return { type: 'separator' };
    if (item.id === 'open-web') return { label: item.label, click: showMainWindow };
    if (item.id === 'open-admin') return { label: item.label, click: openAdmin };
    if (item.id === 'start-desktop-pet' || item.id === 'stop-desktop-pet') return { label: item.label, click: toggleDesktopPet };
    if (item.id === 'service-status') return { label: item.label, click: showServiceStatus };
    if (item.id === 'quit-v8os') return { label: item.label, click: quitV8OS };
    return { label: item.label, enabled: false };
  });
  tray.setContextMenu(Menu.buildFromTemplate(template));
}

function createTray() {
  tray = new Tray(shellIcon());
  tray.setToolTip('V8 Agent OS');
  tray.on('click', showMainWindow);
  updateTrayMenu();
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    title: 'V8 Agent OS',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.loadURL(`${webBaseUrl}/chat`);
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });
  mainWindow.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    mainWindow.hide();
  });
}

app.whenReady().then(() => {
  app.setAppUserModelId('V8OS.LocalShell');
  createMainWindow();
  createTray();
  refreshStatus();
  setInterval(refreshStatus, 5000).unref?.();
});

app.on('activate', () => {
  if (!mainWindow) createMainWindow();
  showMainWindow();
});

app.on('window-all-closed', () => {
  // Keep the product shell resident in the tray.
});
