const { app, BrowserWindow, Menu, Tray, dialog, nativeImage, ipcMain, net, session } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('node:url');
const { buildTrayMenuModel } = require('../lib/tray-menu.cjs');
const { buildStartupHtml } = require('../lib/startup-screen.cjs');

const repoRoot = process.env.V8_REPO_ROOT || (app.isPackaged
  ? path.join(process.resourcesPath, 'v8os')
  : path.resolve(__dirname, '..', '..', '..'));
process.env.V8_REPO_ROOT = repoRoot;
const desktopPetDir = process.env.V8_DESKTOP_PET_DIR || path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet');
process.env.V8_DESKTOP_PET_DIR = desktopPetDir;
const webBaseUrl = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
const adminBaseUrl = process.env.V8_ADMIN_BASE_URL || 'http://127.0.0.1:9528';
const engineBaseUrl = process.env.V8_ENGINE_BASE_URL || 'http://127.0.0.1:9530';
const cliApiUrl = pathToFileURL(path.join(repoRoot, 'apps', 'v8-agent-os-cli', 'src', 'shell_api.mjs')).href;

let mainWindow = null;
let tray = null;
let quitting = false;
let desktopPetRunning = false;
let cliApiPromise = null;
let coreServicesStartPromise = null;

function cliApi() {
  if (!cliApiPromise) {
    cliApiPromise = import(cliApiUrl);
  }
  return cliApiPromise;
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

async function ensureCoreServicesStarted() {
  if (!coreServicesStartPromise) {
    coreServicesStartPromise = cliApi()
      .then(({ shellStart }) => shellStart(['engine', 'admin', 'web'], { mode: 'start' }))
      .catch((error) => {
        coreServicesStartPromise = null;
        throw error;
      });
  }
  return coreServicesStartPromise;
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

function mimeForImagePath(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.webp') return 'image/webp';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.ico') return 'image/x-icon';
  return 'image/png';
}

function productMarkUrl() {
  const found = productImagePath();
  if (!found) return '';
  try {
    const data = fs.readFileSync(found);
    return `data:${mimeForImagePath(found)};base64,${data.toString('base64')}`;
  } catch {
    return '';
  }
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
    await ensureCoreServicesStarted();
    await waitForServices();
    const loggedIn = await isAdminLoggedIn();
    await mainWindow.loadURL(loggedIn ? `${webBaseUrl}/chat` : `${adminBaseUrl}/login`);
  } catch (error) {
    await mainWindow.loadURL(errorDataUrl(error));
  }
}

async function refreshStatus() {
  try {
    const { shellStatus } = await cliApi();
    const statuses = await shellStatus();
    desktopPetRunning = statuses.some((item) => item.id === 'desktop-pet' && item.state === 'managed_running');
  } catch {
    desktopPetRunning = false;
  }
  updateTrayMenu();
}

async function showServiceStatus() {
  let message = '无法读取服务状态。';
  let ok = false;
  try {
    const { shellStatus } = await cliApi();
    message = JSON.stringify(await shellStatus(), null, 2);
    ok = true;
  } catch (error) {
    message = error?.message || message;
  }
  dialog.showMessageBox({
    type: ok ? 'info' : 'warning',
    title: 'V8OS 服务状态',
    message: '当前服务状态',
    detail: message,
  });
}

async function toggleDesktopPet() {
  try {
    const { shellStart, shellStop } = await cliApi();
    if (desktopPetRunning) {
      shellStop(['desktop-pet']);
    } else {
      await shellStart(['desktop-pet'], { mode: 'start' });
    }
  } finally {
    void refreshStatus();
  }
}

async function quitV8OS() {
  quitting = true;
  try {
    const { removeShellProcessRecord, shellStop } = await cliApi();
    shellStop(['engine', 'admin', 'web', 'desktop-pet']);
    removeShellProcessRecord();
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
    if (item.id === 'start-desktop-pet' || item.id === 'stop-desktop-pet') return { label: item.label, click: () => { void toggleDesktopPet(); } };
    if (item.id === 'service-status') return { label: item.label, click: () => { void showServiceStatus(); } };
    if (item.id === 'quit-v8os') return { label: item.label, click: () => { void quitV8OS(); } };
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
  void refreshStatus();
  setInterval(() => { void refreshStatus(); }, 5000).unref?.();
});

app.on('activate', () => {
  if (!mainWindow) createMainWindow();
  showMainWindow();
});

app.on('window-all-closed', () => {
  // Keep the product shell resident in the tray.
});
