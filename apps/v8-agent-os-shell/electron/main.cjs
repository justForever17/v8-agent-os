const { app, BrowserWindow, Menu, Tray, dialog, nativeImage, ipcMain, net, session, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('node:url');
const { createDesktopPetShutdownCoordinator } = require('../lib/desktop-pet-shutdown.cjs');
const { createShellControlServer, isValidSessionId } = require('../lib/shell-control.cjs');
const { parseShellDeepLink } = require('../lib/shell-route.cjs');
const { buildTrayMenuModel } = require('../lib/tray-menu.cjs');
const { buildStartupHtml } = require('../lib/startup-screen.cjs');
const { loadUrlSafely } = require('../lib/navigation-load.cjs');
const {
  classifyProductSurface,
  fetchTextWithTimeout,
  validateReadinessResponse,
  verifyProductSurfaceDom,
} = require('../lib/readiness-probe.cjs');

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
let shellProcessRecordIdentity = null;
let cliApiPromise = null;
let coreServicesStartPromise = null;
let coreServicesReady = false;
let statusRefreshPromise = null;
let pendingSurfaceUrl = null;
let lastPublishedControlStatus = '';
let surfaceRecoveryTimer = null;
let surfaceStabilityTimer = null;
let surfaceRecoveryTimes = [];
const SURFACE_RECOVERY_WINDOW_MS = 60_000;
const MAX_SURFACE_RECOVERY_ATTEMPTS = 2;
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
  if (mainWindow.isMinimized()) mainWindow.restore();
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
  shellControl?.setSurfaceStatus({ surfaceReady: false });
  if (!mainWindow) {
    createMainWindow();
  }
  showMainWindow();
  if (!coreServicesReady) return Promise.resolve(false);
  pendingSurfaceUrl = null;
  return loadUrlSafely(
    () => mainWindow.loadURL(url),
    (error) => scheduleSurfaceRecovery(`navigation: ${error?.message || 'load failed'}`, url),
  );
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
  return loadInMainWindow(`${webBaseUrl}/chat?id=${encodeURIComponent(sessionId)}`);
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

async function waitForUrl(url, options = {}) {
  const timeoutMs = options.timeoutMs ?? 90000;
  const intervalMs = options.intervalMs ?? 700;
  const startedAt = Date.now();
  let lastError = null;
  let lastReportedReason = null;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const expectedOrigin = options.expectedOrigin || new URL(url).origin;
      const { response, body, responseUrl } = await fetchTextWithTimeout(
        net.fetch.bind(net),
        url,
        options.fetchTimeoutMs ?? 1800,
        {
          cache: 'no-store',
          credentials: 'omit',
          headers: { Accept: 'text/html' },
        },
      );
      const validation = validateReadinessResponse(options.kind, {
        ok: response.ok,
        status: response.status,
        contentType: response.headers.get('content-type') || '',
        body,
        responseUrl,
        expectedOrigin,
      });
      if (validation.ok) return true;
      lastError = new Error(validation.reason);
      if (validation.reason !== lastReportedReason) {
        lastReportedReason = validation.reason;
        reportSurfaceStage('readiness_probe_waiting', {
          service: options.kind || 'unknown',
          reason: validation.reason,
        });
      }
    } catch (error) {
      lastError = error;
      const reason = error?.name === 'AbortError' ? 'request_timeout' : 'request_failed';
      if (reason !== lastReportedReason) {
        lastReportedReason = reason;
        reportSurfaceStage('readiness_probe_waiting', {
          service: options.kind || 'unknown',
          reason,
        });
      }
    }
    await sleep(intervalMs);
  }
  throw new Error(`等待服务就绪超时：${url}${lastError ? ` (${lastError.message})` : ''}`);
}

async function waitForServices() {
  await Promise.all([
    waitForUrl(`${engineBaseUrl}/readyz`, { timeoutMs: 120000, kind: 'engine' })
      .then(() => reportSurfaceStage('readiness_probe_ready', { service: 'engine' })),
    waitForUrl(`${adminBaseUrl}/login`, { timeoutMs: 120000, kind: 'admin', expectedOrigin: new URL(adminBaseUrl).origin })
      .then(() => reportSurfaceStage('readiness_probe_ready', { service: 'admin' })),
    waitForUrl(`${webBaseUrl}/chat`, { timeoutMs: 120000, kind: 'web' })
      .then(() => reportSurfaceStage('readiness_probe_ready', { service: 'web' })),
  ]);
}

async function ensureCoreServicesStarted() {
  if (!coreServicesStartPromise) {
    coreServicesStartPromise = cliApi()
      .then(async ({ shellStart }) => {
        const results = await shellStart(['engine', 'admin', 'web'], { mode: 'start' });
        const failures = results.filter((item) => !['started', 'already_running'].includes(item.status));
        if (failures.length > 0) {
          throw new Error(`核心服务启动失败：${failures.map((item) => `${item.id}:${item.status}`).join(', ')}`);
        }
        return results;
      })
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
    const { response, body } = await fetchTextWithTimeout(
      net.fetch.bind(net),
      `${adminBaseUrl}/api/auth/session`,
      2000,
      {
        cache: 'no-store',
        headers: cookieHeader ? { Cookie: cookieHeader } : undefined,
      },
    );
    if (!response.ok) {
      return false;
    }
    const payload = JSON.parse(body || '{}');
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

function reportSurfaceStage(stage, details = {}) {
  console.log('[v8os-shell] surface', JSON.stringify({ stage, ...details }));
}

function isLocalProductSurface(url) {
  try {
    const origin = new URL(String(url || '')).origin;
    return origin === new URL(webBaseUrl).origin || origin === new URL(adminBaseUrl).origin;
  } catch {
    return false;
  }
}

function scheduleSurfaceRecovery(reason, targetUrl = '') {
  shellControl?.setSurfaceStatus({ surfaceReady: false });
  if (quitting || !mainWindow || mainWindow.isDestroyed() || surfaceRecoveryTimer) return;
  const now = Date.now();
  surfaceRecoveryTimes = surfaceRecoveryTimes.filter((timestamp) => now - timestamp < SURFACE_RECOVERY_WINDOW_MS);
  if (surfaceRecoveryTimes.length >= MAX_SURFACE_RECOVERY_ATTEMPTS) {
    console.error(`[v8os-shell] surface recovery stopped: ${reason}`);
    void mainWindow.loadURL(errorDataUrl(`界面连续恢复失败（${reason}）`)).catch(() => undefined);
    return;
  }
  surfaceRecoveryTimes.push(now);
  if (surfaceStabilityTimer) {
    clearTimeout(surfaceStabilityTimer);
    surfaceStabilityTimer = null;
  }
  if (isLocalProductSurface(targetUrl)) pendingSurfaceUrl = targetUrl;
  console.error(`[v8os-shell] recovering local surface: ${reason}`);
  void mainWindow.loadURL(startupDataUrl('界面进程正在恢复，正在重新连接 Engine / Admin / Web...')).catch(() => undefined);
  surfaceRecoveryTimer = setTimeout(() => {
    surfaceRecoveryTimer = null;
    if (!quitting && mainWindow && !mainWindow.isDestroyed()) void loadInitialSurface();
  }, 700);
}

async function loadInitialSurface() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  shellControl?.setSurfaceStatus({ surfaceReady: false });
  reportSurfaceStage('starting_core_services');
  try {
    await ensureCoreServicesStarted();
    reportSurfaceStage('waiting_for_readiness');
    await waitForServices();
    reportSurfaceStage('core_services_ready');
    const loggedIn = await isAdminLoggedIn();
    coreServicesReady = true;
    const defaultChatUrl = activeSessionId ? `${webBaseUrl}/chat?id=${encodeURIComponent(activeSessionId)}` : `${webBaseUrl}/chat`;
    const targetUrl = pendingSurfaceUrl || (loggedIn ? defaultChatUrl : `${adminBaseUrl}/login`);
    const expectedSurfaceKind = classifyProductSurface({
      coreServicesReady: true,
      loadedUrl: targetUrl,
      webBaseUrl,
      adminBaseUrl,
    });
    reportSurfaceStage('loading_product_surface', { expectedSurfaceKind });
    pendingSurfaceUrl = null;
    let navigationError = null;
    const loaded = await loadUrlSafely(
      () => mainWindow.loadURL(targetUrl),
      (error) => { navigationError = error; },
    );
    if (!loaded && navigationError) {
      await loadUrlSafely(
        () => mainWindow.loadURL(errorDataUrl(navigationError)),
        (fallbackError) => console.error('[v8os-shell] failed to show startup error surface', fallbackError),
      );
    } else if (loaded) {
      reportSurfaceStage('product_navigation_completed', { expectedSurfaceKind });
    }
  } catch (error) {
    reportSurfaceStage('startup_failed', { reason: error?.message || String(error) });
    shellControl?.setSurfaceStatus({ surfaceReady: false });
    await loadUrlSafely(
      () => mainWindow.loadURL(errorDataUrl(error)),
      (fallbackError) => console.error('[v8os-shell] failed to show startup error surface', fallbackError),
    );
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

async function refreshStatusOnce() {
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

function refreshStatus() {
  if (statusRefreshPromise) return statusRefreshPromise;
  statusRefreshPromise = refreshStatusOnce().finally(() => {
    statusRefreshPromise = null;
  });
  return statusRefreshPromise;
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
    await shellStop(['desktop-pet']);
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
    const firstStop = await shellStop(coreIds, stopOptions);
    let remaining = await waitForCoreServicesStopped(shellStatus);
    if (remaining.some((item) => item.pidAlive || item.portOpen)) {
      const retryStop = await shellStop(coreIds, stopOptions);
      remaining = await waitForCoreServicesStopped(shellStatus, 10);
      console.warn('[V8OS Shell] Core shutdown required reconciliation', { firstStop, retryStop, remaining });
    }
    if (remaining.some((item) => item.pidAlive || item.portOpen)) {
      console.error('[V8OS Shell] Core services remain after shutdown', { remaining });
    }
    await removeShellProcessRecord(shellProcessRecordIdentity);
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
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    if (details?.reason === 'clean-exit') return;
    shellControl?.setSurfaceStatus({ surfaceReady: false });
    scheduleSurfaceRecovery(`renderer ${details?.reason || 'gone'} (${details?.exitCode ?? 'unknown'})`, mainWindow?.webContents.getURL());
  });
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
    if (!isMainFrame || errorCode === -3 || !isLocalProductSurface(validatedUrl)) return;
    shellControl?.setSurfaceStatus({ surfaceReady: false });
    scheduleSurfaceRecovery(`load ${errorCode}: ${errorDescription}`, validatedUrl);
  });
  mainWindow.webContents.on('did-finish-load', () => {
    const contents = mainWindow?.webContents;
    const loadedUrl = contents?.getURL() || '';
    const surfaceKind = classifyProductSurface({
      coreServicesReady,
      loadedUrl,
      webBaseUrl,
      adminBaseUrl,
    });
    if (!surfaceKind || !contents) {
      shellControl?.setSurfaceStatus({ surfaceReady: false });
      reportSurfaceStage('surface_loaded', { surfaceKind: null, domReady: false });
      return;
    }
    void verifyProductSurfaceDom(
      (script) => contents.executeJavaScript(script, true),
      surfaceKind,
    ).then((domReady) => {
      if (contents.isDestroyed() || mainWindow?.webContents !== contents) return;
      const currentUrl = contents.getURL() || '';
      const currentSurfaceKind = classifyProductSurface({
        coreServicesReady,
        loadedUrl: currentUrl,
        webBaseUrl,
        adminBaseUrl,
      });
      const surfaceReady = domReady && currentSurfaceKind === surfaceKind && currentUrl === loadedUrl;
      shellControl?.setSurfaceStatus({
        surfaceReady,
        surfaceKind: surfaceReady ? surfaceKind : null,
      });
      reportSurfaceStage('surface_loaded', {
        surfaceKind: surfaceReady ? surfaceKind : null,
        domReady: surfaceReady,
      });
      if (!surfaceReady) {
        scheduleSurfaceRecovery('product DOM marker missing', currentUrl);
        return;
      }
      if (surfaceStabilityTimer) clearTimeout(surfaceStabilityTimer);
      surfaceStabilityTimer = setTimeout(() => {
        surfaceRecoveryTimes = [];
        surfaceStabilityTimer = null;
      }, 15_000);
    });
  });
  void loadUrlSafely(
    () => mainWindow.loadURL(startupDataUrl('正在启动 Engine / Admin / Web...')),
    (error) => console.error('[v8os-shell] failed to show startup surface', error),
  ).finally(() => {
    void loadInitialSurface();
  });
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

ipcMain.handle('v8os-shell:reveal-workspace-file', async (_event, workspaceRelativePath, workspacePath) => {
  const requestedRelativePath = String(workspaceRelativePath || '').trim();
  const requestedRoot = String(workspacePath || '').trim();
  if (
    !requestedRelativePath
    || !requestedRoot
    || requestedRelativePath.length > 4096
    || requestedRoot.length > 4096
    || path.isAbsolute(requestedRelativePath)
    || !path.isAbsolute(requestedRoot)
  ) {
    return { ok: false, error: 'invalid_workspace_file_path' };
  }
  const resolvedRoot = path.resolve(requestedRoot);
  const resolvedFile = path.resolve(resolvedRoot, requestedRelativePath);
  const relative = path.relative(resolvedRoot, resolvedFile);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    return { ok: false, error: 'workspace_file_outside_root' };
  }
  try {
    if (!fs.statSync(resolvedRoot).isDirectory() || !fs.statSync(resolvedFile).isFile()) {
      return { ok: false, error: 'workspace_file_not_found' };
    }
  } catch {
    return { ok: false, error: 'workspace_file_not_found' };
  }
  shell.showItemInFolder(resolvedFile);
  return { ok: true };
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
      const { getShellProcessRecordIdentity } = await cliApi();
      shellProcessRecordIdentity = getShellProcessRecordIdentity();
    } catch (error) {
      console.warn('[V8OS Shell] Unable to capture managed Shell identity', { reason: error?.message || 'unknown_error' });
    }
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
    setInterval(() => { void refreshStatus(); }, 10_000).unref?.();
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
