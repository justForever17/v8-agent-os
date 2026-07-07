const { app, BrowserWindow, Menu, Tray, dialog, nativeImage, ipcMain, net, session } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { pathToFileURL } = require('url');
const { buildTrayMenuModel } = require('../lib/tray-menu.cjs');
const { buildStartupHtml } = require('../lib/startup-screen.cjs');

const repoRoot = process.env.V8_REPO_ROOT || path.resolve(__dirname, '..', '..', '..');
const desktopPetDir = process.env.V8_DESKTOP_PET_DIR || path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet');
const webBaseUrl = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
const adminBaseUrl = process.env.V8_ADMIN_BASE_URL || 'http://127.0.0.1:9528';
const engineBaseUrl = process.env.V8_ENGINE_BASE_URL || 'http://127.0.0.1:9530';
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
  const iconPath = productImagePath();
  if (iconPath) {
    const image = nativeImage.createFromPath(iconPath);
    if (!image.isEmpty()) return image;
  }
  return nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAF/wJ+gZtC6QAAAABJRU5ErkJggg==');
}

function showMainWindow() {
  if (!mainWindow) return;
  mainWindow.show();
  mainWindow.focus();
}

function loadInMainWindow(url) {
  if (!mainWindow) {
    createMainWindow();
  }
  showMainWindow();
  return mainWindow.loadURL(url);
}

async function openAdmin() {
  return loadInMainWindow(`${adminBaseUrl}/admin`);
}

async function openWeb() {
  const loggedIn = await isAdminLoggedIn();
  return loadInMainWindow(loggedIn ? `${webBaseUrl}/chat` : `${adminBaseUrl}/login`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithTimeout(url, timeoutMs = 1500, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await net.fetch(url, {
      signal: controller.signal,
      cache: 'no-store',
      headers: options.headers,
    });
  } finally {
    clearTimeout(timeout);
  }
}

async function waitForUrl(url, options = {}) {
  const timeoutMs = options.timeoutMs ?? 90000;
  const intervalMs = options.intervalMs ?? 700;
  const startedAt = Date.now();
  let lastError = null;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetchWithTimeout(url, options.fetchTimeoutMs ?? 1800);
      if (response.status >= 200 && response.status < 500) {
        return true;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await sleep(intervalMs);
  }
  throw new Error(`等待服务就绪超时：${url}${lastError ? ` (${lastError.message})` : ''}`);
}

async function waitForServices() {
  await waitForUrl(`${engineBaseUrl}/health`, { timeoutMs: 120000 });
  await waitForUrl(`${adminBaseUrl}/login`, { timeoutMs: 120000 });
  await waitForUrl(`${webBaseUrl}/chat`, { timeoutMs: 120000 });
}

async function isAdminLoggedIn() {
  try {
    const cookies = await session.defaultSession.cookies.get({ url: adminBaseUrl });
    const cookieHeader = cookies
      .map((cookie) => `${cookie.name}=${cookie.value}`)
      .join('; ');
    const response = await fetchWithTimeout(`${adminBaseUrl}/api/auth/session`, 2000, {
      headers: cookieHeader ? { Cookie: cookieHeader } : undefined,
    });
    if (!response.ok) {
      return false;
    }
    const payload = await response.json().catch(() => ({}));
    return payload?.user?.role === 'ADMIN' || Boolean(payload?.user?.email);
  } catch {
    return false;
  }
}

function productImagePath() {
  const candidates = [
    path.join(repoRoot, 'apps', 'v8-agent-os-web', 'public', 'product-mark.png'),
    path.join(repoRoot, 'apps', 'v8-agent-os-admin', 'public', 'product-mark.png'),
    path.join(repoRoot, 'apps', 'v8-agent-os-web', 'public', 'icon.png'),
    path.join(repoRoot, 'apps', 'v8-agent-os-admin', 'public', 'icon.png'),
    path.join(desktopPetDir, 'electron', 'assets', 'tray-icon.png'),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || '';
}

function productMarkUrl() {
  const found = productImagePath();
  return found ? pathToFileURL(found).href : '';
}

function startupDataUrl(detail) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(buildStartupHtml({
    markUrl: productMarkUrl(),
    detail,
  }))}`;
}

function errorDataUrl(error) {
  return startupDataUrl(`启动未完成：${error?.message || error || '未知错误'}。请从托盘查看服务状态或运行 v8os doctor。`);
}

async function loadInitialSurface() {
  if (!mainWindow) return;
  try {
    await waitForServices();
    const loggedIn = await isAdminLoggedIn();
    await mainWindow.loadURL(loggedIn ? `${webBaseUrl}/chat` : `${adminBaseUrl}/login`);
  } catch (error) {
    await mainWindow.loadURL(errorDataUrl(error));
  }
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
    if (item.id === 'open-web') return { label: item.label, click: openWeb };
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
    frame: false,
    icon: productImagePath() || undefined,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  mainWindow.loadURL(startupDataUrl('正在启动 Engine / Admin / Web...'));
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });
  mainWindow.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    mainWindow.hide();
  });
  loadInitialSurface();
}

ipcMain.on('v8os-shell:minimize', () => {
  mainWindow?.minimize();
});

ipcMain.on('v8os-shell:toggle-maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});

ipcMain.on('v8os-shell:close', () => {
  mainWindow?.hide();
});

ipcMain.on('v8os-shell:open-web', () => {
  void openWeb();
});

ipcMain.on('v8os-shell:open-admin', () => {
  void openAdmin();
});

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
