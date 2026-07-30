const { app, BrowserWindow, Menu, Tray, dialog, nativeImage, ipcMain, net, session, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('node:url');
const { createDesktopPetShutdownCoordinator } = require('../lib/desktop-pet-shutdown.cjs');
const { createShellControlServer, isValidSessionId } = require('../lib/shell-control.cjs');
const { parseShellDeepLink } = require('../lib/shell-route.cjs');
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
let desktopPetState = 'stopped';
let desktopPetStateChangedAt = Date.now();
let desktopPetProcessRunning = false;
let activeSessionId = null;
let desktopPetActiveSessionId = null;
let shellControl = null;
let cliApiPromise = null;
let coreServicesStartPromise = null;
let coreServicesReady = false;
let pendingSurfaceUrl = null;
let lastPublishedControlStatus = '';
const desktopPetShutdown = createDesktopPetShutdownCoordinator();

function currentDesktopPetStatus() {
  return {
    state: desktopPetState,
    processRunning: desktopPetProcessRunning,
    controlConnected: Boolean(shellControl?.hasAuthenticatedClient()),
    activeSessionId: desktopPetActiveSessionId,
    enabled: desktopPetProcessRunning
      || Boolean(shellControl?.hasAuthenticatedClient())
      || ['starting', 'waiting_v8os', 'connected', 'stopping'].includes(desktopPetState),
  };
}

function emitDesktopPetStatus() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('v8os-shell:desktop-pet-state', currentDesktopPetStatus());
}

function publishShellControlStatus() {
  const current = currentDesktopPetStatus();
  const status = {
    desktopPetState: current.state,
    desktopPetProcessRunning: current.processRunning,
    controlConnected: current.controlConnected,
    desktopPetActiveSessionId: current.activeSessionId,
  };
  const fingerprint = JSON.stringify(status);
  if (fingerprint === lastPublishedControlStatus) return;
  emitDesktopPetStatus();
  if (shellControl?.setRuntimeStatus(status)) lastPublishedControlStatus = fingerprint;
}

function setDesktopPetState(nextState) {
  if (desktopPetState === nextState) return;
  desktopPetState = nextState;
  desktopPetStateChangedAt = Date.now();
  publishShellControlStatus();
  updateTrayMenu();
}

function cliApi() {
  if (!cliApiPromise) {
    cliApiPromise = import(cliApiUrl);
  }
  return cliApiPromise;
}

function shellIcon() {
  const iconPath = trayIconPath();
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

function currentWindowState() {
  return { isMaximized: Boolean(mainWindow && !mainWindow.isDestroyed() && mainWindow.isMaximized()) };
}

function emitWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('v8os-shell:window-state', currentWindowState());
}

function loadInMainWindow(url) {
  pendingSurfaceUrl = url;
  if (!mainWindow) {
    createMainWindow();
  }
  showMainWindow();
  if (!coreServicesReady) return Promise.resolve(false);
  pendingSurfaceUrl = null;
  return mainWindow.loadURL(url);
}

async function openAdmin() {
  return loadInMainWindow(`${adminBaseUrl}/admin`);
}

async function openDesktopPetSettings() {
  return loadInMainWindow(`${adminBaseUrl}/admin/desktop-pet`);
}

async function openWeb() {
  const loggedIn = await isAdminLoggedIn();
  const chatUrl = activeSessionId ? `${webBaseUrl}/chat?id=${encodeURIComponent(activeSessionId)}` : `${webBaseUrl}/chat`;
  return loadInMainWindow(loggedIn ? chatUrl : `${adminBaseUrl}/login`);
}

async function openWebSession(sessionId) {
  if (!isValidSessionId(sessionId)) return false;
  await loadInMainWindow(`${webBaseUrl}/chat?id=${encodeURIComponent(sessionId)}`);
  return true;
}

function handleShellDeepLink(rawUrl) {
  const route = parseShellDeepLink(rawUrl);
  if (!route) return false;
  if (route.surface === 'admin' && route.path === '/admin/desktop-pet') {
    void openDesktopPetSettings();
    return true;
  }
  return false;
}

function deepLinkFromArgv(argv = process.argv) {
  return argv.find((value) => String(value || '').startsWith('v8os://')) || '';
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

function shellAssetPath(name) {
  const candidate = path.resolve(__dirname, '..', 'assets', name);
  return fs.existsSync(candidate) ? candidate : '';
}

function productMarkPath() {
  const candidates = [
    path.join(repoRoot, 'apps', 'v8-agent-os-web', 'public', 'product-mark.png'),
    path.join(repoRoot, 'apps', 'v8-agent-os-admin', 'public', 'product-mark.png'),
    shellAssetPath('icon.png'),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) || '';
}

function taskbarIconPath() {
  const candidates = [
    shellAssetPath(process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    shellAssetPath('icon.png'),
    path.join(repoRoot, 'apps', 'v8-agent-os-web', 'public', 'icon.png'),
    path.join(repoRoot, 'apps', 'v8-agent-os-admin', 'public', 'icon.png'),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) || '';
}

function trayIconPath() {
  const candidates = [
    shellAssetPath('tray-icon.png'),
    shellAssetPath('icon.png'),
    path.join(desktopPetDir, 'electron', 'assets', 'tray-icon.png'),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) || taskbarIconPath();
}

function mimeForImagePath(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.webp') return 'image/webp';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.ico') return 'image/x-icon';
  return 'image/png';
}

function productMarkUrl() {
  const found = productMarkPath();
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
    coreServicesReady = true;
    const defaultChatUrl = activeSessionId ? `${webBaseUrl}/chat?id=${encodeURIComponent(activeSessionId)}` : `${webBaseUrl}/chat`;
    const targetUrl = pendingSurfaceUrl || (loggedIn ? defaultChatUrl : `${adminBaseUrl}/login`);
    pendingSurfaceUrl = null;
    await mainWindow.loadURL(targetUrl);
  } catch (error) {
    await mainWindow.loadURL(errorDataUrl(error));
  }
}

async function startShellControl() {
  shellControl = createShellControlServer({
    onAuthenticated() {
      if (desktopPetState !== 'stopping') setDesktopPetState('waiting_v8os');
      publishShellControlStatus();
      shellControl?.send('active-session', { sessionId: activeSessionId });
    },
    onDisconnect() {
      if (desktopPetState === 'stopping') {
        publishShellControlStatus();
        return;
      }
      setDesktopPetState(desktopPetProcessRunning ? 'error' : 'stopped');
      publishShellControlStatus();
    },
    onMessage(message) {
      if (message.type === 'pet-status') {
        desktopPetActiveSessionId = isValidSessionId(message.activeSessionId)
          ? String(message.activeSessionId).trim()
          : null;
        setDesktopPetState(message.state);
        publishShellControlStatus();
        return;
      }
      if (message.type === 'open-settings') {
        void openDesktopPetSettings();
        return;
      }
      if (message.type === 'open-session') {
        void openWebSession(message.sessionId);
        return;
      }
      if (message.type === 'shutdown-ready') {
        desktopPetShutdown.acknowledge(message.requestId);
      }
    },
  });
  const restored = await shellControl.start();
  if (!activeSessionId && isValidSessionId(restored?.previousActiveSessionId)) {
    activeSessionId = String(restored.previousActiveSessionId).trim();
    shellControl.send('active-session', { sessionId: activeSessionId });
  }
  publishShellControlStatus();
}

function reportActiveSession(sessionId) {
  const normalized = isValidSessionId(sessionId) ? String(sessionId).trim() : null;
  activeSessionId = normalized;
  shellControl?.setActiveSession(normalized);
  shellControl?.send('active-session', { sessionId: normalized });
}

async function refreshStatus() {
  try {
    const { shellStatus } = await cliApi();
    const statuses = await shellStatus(['desktop-pet']);
    desktopPetProcessRunning = statuses.some((item) => item.id === 'desktop-pet' && item.state === 'managed_running');
  } catch {
    if (desktopPetProcessRunning) setDesktopPetState('error');
    updateTrayMenu();
    return;
  }

  if (shellControl?.hasAuthenticatedClient()) {
    if (desktopPetState === 'stopped' || desktopPetState === 'starting' || desktopPetState === 'error') {
      setDesktopPetState('waiting_v8os');
    }
  } else if (!desktopPetProcessRunning) {
    setDesktopPetState('stopped');
  } else if (desktopPetState === 'stopped') {
    setDesktopPetState('starting');
  } else if (desktopPetState === 'starting' && Date.now() - desktopPetStateChangedAt > 10000) {
    setDesktopPetState('error');
  } else if (desktopPetState === 'stopping' && Date.now() - desktopPetStateChangedAt > 3000) {
    setDesktopPetState('error');
  } else if (desktopPetState === 'waiting_v8os' || desktopPetState === 'connected') {
    setDesktopPetState('error');
  }
  publishShellControlStatus();
  updateTrayMenu();
}

async function showServiceStatus() {
  let message = '无法读取服务状态。';
  let ok = false;
  try {
    const { shellStatus } = await cliApi();
    message = JSON.stringify({
      desktopPet: {
        state: desktopPetState,
        processRunning: desktopPetProcessRunning,
        controlConnected: Boolean(shellControl?.hasAuthenticatedClient()),
        activeSessionId,
        desktopPetActiveSessionId,
      },
      services: await shellStatus(),
    }, null, 2);
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

async function stopDesktopPetGracefully() {
  if (!desktopPetProcessRunning && !shellControl?.hasAuthenticatedClient()) {
    setDesktopPetState('stopped');
    return { acked: true, reason: 'already_stopped' };
  }
  setDesktopPetState('stopping');
  const result = await desktopPetShutdown.request(
    (requestId) => (shellControl?.send('shutdown', { requestId }) || 0) > 0,
    1500,
  );
  if (!result.acked) {
    console.warn('[V8OS Shell] Desktop pet graceful shutdown failed; using CLI fallback', { reason: result.reason });
    const { shellStop } = await cliApi();
    shellStop(['desktop-pet']);
  }
  setTimeout(() => { void refreshStatus(); }, result.acked ? 300 : 0).unref?.();
  return result;
}

async function setDesktopPetEnabled(enabled) {
  if (desktopPetState === 'starting' || desktopPetState === 'stopping') return currentDesktopPetStatus();
  try {
    const shouldStop = desktopPetProcessRunning || shellControl?.hasAuthenticatedClient();
    if (!enabled && shouldStop) {
      await stopDesktopPetGracefully();
    } else if (enabled && !shouldStop) {
      setDesktopPetState('starting');
      const { shellStart } = await cliApi();
      const results = await shellStart(['desktop-pet'], { mode: 'start' });
      const accepted = results.some((item) => item.status === 'started' || item.status === 'already_running');
      if (!accepted) setDesktopPetState('error');
    }
  } catch (error) {
    console.warn('[V8OS Shell] Desktop pet toggle failed', { reason: error?.message || 'unknown_error' });
    setDesktopPetState('error');
  } finally {
    void refreshStatus();
  }
  return currentDesktopPetStatus();
}

async function toggleDesktopPet() {
  const shouldStop = desktopPetProcessRunning || shellControl?.hasAuthenticatedClient();
  return setDesktopPetEnabled(!shouldStop);
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForCoreServicesStopped(shellStatus, attempts = 15) {
  let statuses = [];
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    statuses = await shellStatus(['engine', 'admin', 'web']);
    if (statuses.every((item) => !item.pidAlive && !item.portOpen)) return statuses;
    await wait(100);
  }
  return statuses;
}

async function quitV8OS() {
  if (quitting) return;
  quitting = true;
  try {
    if (desktopPetProcessRunning || shellControl?.hasAuthenticatedClient()) {
      await stopDesktopPetGracefully();
    }
  } catch (error) {
    console.error('[V8OS Shell] Desktop pet shutdown phase failed; core shutdown will continue', {
      reason: error?.message || 'unknown_error',
    });
  }
  try {
    const { removeShellProcessRecord, shellStatus, shellStop } = await cliApi();
    const coreIds = ['engine', 'admin', 'web'];
    const stopOptions = { stopVerifiedPortOwners: coreIds };
    const firstStop = shellStop(coreIds, stopOptions);
    let remaining = await waitForCoreServicesStopped(shellStatus);
    if (remaining.some((item) => item.pidAlive || item.portOpen)) {
      const retryStop = shellStop(coreIds, stopOptions);
      remaining = await waitForCoreServicesStopped(shellStatus, 10);
      console.warn('[V8OS Shell] Core shutdown required reconciliation', { firstStop, retryStop, remaining });
    }
    if (remaining.some((item) => item.pidAlive || item.portOpen)) {
      console.error('[V8OS Shell] Core services remain after shutdown', { remaining });
    }
    removeShellProcessRecord();
  } catch (error) {
    console.error('[V8OS Shell] Core shutdown phase failed', { reason: error?.message || 'unknown_error' });
  } finally {
    await shellControl?.stop().catch((error) => {
      console.warn('[V8OS Shell] Control channel shutdown failed', { reason: error?.message || 'unknown_error' });
    });
    app.quit();
  }
}

function updateTrayMenu() {
  if (!tray) return;
  const model = buildTrayMenuModel({ desktopPetState, desktopPetProcessRunning });
  const template = model.map((item) => {
    if (item.type === 'separator') return { type: 'separator' };
    if (item.id === 'open-web') return { label: item.label, click: openWeb };
    if (item.id === 'open-admin') return { label: item.label, click: openAdmin };
    if (item.id === 'start-desktop-pet' || item.id === 'stop-desktop-pet') return { label: item.label, enabled: item.enabled !== false, click: () => { void toggleDesktopPet(); } };
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
    icon: taskbarIconPath() || undefined,
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
  mainWindow.on('maximize', emitWindowState);
  mainWindow.on('unmaximize', emitWindowState);
  mainWindow.on('restore', emitWindowState);
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
  emitWindowState();
});

ipcMain.handle('v8os-shell:get-window-state', () => currentWindowState());

ipcMain.on('v8os-shell:close', () => {
  mainWindow?.hide();
});

ipcMain.on('v8os-shell:open-web', () => {
  void openWeb();
});

ipcMain.on('v8os-shell:open-admin', () => {
  void openAdmin();
});

ipcMain.on('v8os-shell:active-session', (_event, sessionId) => {
  reportActiveSession(sessionId);
});

ipcMain.handle('v8os-shell:open-workspace-folder', async (_event, workspacePath) => {
  const requestedPath = String(workspacePath || '').trim();
  if (!requestedPath || requestedPath.length > 4096 || !path.isAbsolute(requestedPath)) {
    return { ok: false, error: 'invalid_workspace_path' };
  }
  const resolvedPath = path.resolve(requestedPath);
  try {
    if (!fs.statSync(resolvedPath).isDirectory()) {
      return { ok: false, error: 'workspace_path_not_directory' };
    }
  } catch {
    return { ok: false, error: 'workspace_path_not_found' };
  }
  const error = await shell.openPath(resolvedPath);
  return error ? { ok: false, error } : { ok: true };
});

ipcMain.handle('v8os-shell:select-godot-executable', async () => {
  if (!mainWindow) return { ok: false, error: 'shell_window_unavailable' };
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select Godot executable',
    properties: ['openFile'],
    filters: process.platform === 'win32'
      ? [{ name: 'Godot', extensions: ['exe'] }]
      : [{ name: 'Godot', extensions: ['*'] }],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: false, cancelled: true };
  return { ok: true, path: path.resolve(result.filePaths[0]) };
});

ipcMain.handle('v8os-shell:select-godot-project-directory', async () => {
  if (!mainWindow) return { ok: false, error: 'shell_window_unavailable' };
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select Godot project',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: false, cancelled: true };
  return { ok: true, path: path.resolve(result.filePaths[0]) };
});

ipcMain.handle('v8os-shell:get-desktop-pet-state', async () => {
  await refreshStatus();
  return currentDesktopPetStatus();
});

ipcMain.handle('v8os-shell:set-desktop-pet-enabled', async (_event, enabled) => {
  await refreshStatus();
  return setDesktopPetEnabled(Boolean(enabled));
});

function registerShellProtocol() {
  if (process.defaultApp && process.argv.length >= 2) {
    return app.setAsDefaultProtocolClient('v8os', process.execPath, [path.resolve(process.argv[1])]);
  }
  return app.setAsDefaultProtocolClient('v8os');
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    const deepLink = deepLinkFromArgv(argv);
    if (!handleShellDeepLink(deepLink)) showMainWindow();
  });

  app.whenReady().then(async () => {
    app.setAppUserModelId('V8OS.LocalShell');
    registerShellProtocol();
    try {
      await startShellControl();
    } catch (error) {
      console.warn('[V8OS Shell] Local control channel unavailable', { reason: error?.message || 'unknown_error' });
    }
    createMainWindow();
    createTray();
    const initialDeepLink = deepLinkFromArgv();
    if (initialDeepLink) handleShellDeepLink(initialDeepLink);
    void refreshStatus();
    setInterval(() => { void refreshStatus(); }, 1000).unref?.();
  });

  app.on('activate', () => {
    if (!mainWindow) createMainWindow();
    showMainWindow();
  });
}

app.on('open-url', (event, url) => {
  event.preventDefault();
  handleShellDeepLink(url);
});

app.on('will-quit', () => {
  desktopPetShutdown.cancelAll();
  void shellControl?.stop();
});

app.on('window-all-closed', () => {
  // Keep the product shell resident in the tray.
});
