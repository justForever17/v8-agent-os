const { app, BrowserWindow, Menu, Notification, Tray, dialog, nativeImage, ipcMain, net, shell } = require('electron');
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
  classifyWindowOpen,
  isTrustedIpcSource,
  isTrustedProductUrl,
  trustedProductOrigins,
} = require('../lib/surface-security.cjs');
const {
  classifyProductSurface,
  fetchTextWithTimeout,
  initialProductSurfaceUrl,
  validateReadinessResponse,
  waitForProductSurfaceDom,
} = require('../lib/readiness-probe.cjs');
const {
  checkForDesktopUpdate,
  loadReleaseIdentity,
  releaseUrlForTag,
} = require('../lib/update-check.cjs');

const repoRoot = process.env.V8_REPO_ROOT || (app.isPackaged
  ? path.join(process.resourcesPath, 'v8os')
  : path.resolve(__dirname, '..', '..', '..'));
process.env.V8_REPO_ROOT = repoRoot;
const desktopPetDir = process.env.V8_DESKTOP_PET_DIR || path.join(repoRoot, 'apps', 'v8-agent-os-desktop-pet');
process.env.V8_DESKTOP_PET_DIR = desktopPetDir;
let webBaseUrl = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
let adminBaseUrl = process.env.V8_ADMIN_BASE_URL || 'http://127.0.0.1:9528';
let engineBaseUrl = process.env.V8_ENGINE_BASE_URL || 'http://127.0.0.1:9530';
const cliApiUrl = pathToFileURL(path.join(repoRoot, 'apps', 'v8-agent-os-cli', 'src', 'shell_api.mjs')).href;
const releaseManifestPath = path.join(repoRoot, 'release-manifest.json');
let productOrigins = trustedProductOrigins([webBaseUrl, adminBaseUrl]);
const CORE_SERVICE_IDS = ['engine', 'admin', 'web'];
const CORE_SERVICE_LABELS = { engine: 'Engine', admin: 'Admin', web: 'Web' };
const MANAGED_SHELL_SHUTDOWN_ARG = '--v8os-managed-shutdown';
const updateChecksEnabled = app.isPackaged && process.env.V8OS_DISABLE_UPDATE_CHECK !== '1';
const AUTOMATIC_UPDATE_CHECK_DELAY_MS = 20_000;

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
let initialSurfaceLoadPromise = null;
let lastStartupFailure = null;
let coreServicesReady = false;
let statusRefreshPromise = null;
let pendingSurfaceUrl = null;
let lastPublishedControlStatus = '';
let surfaceRecoveryTimer = null;
let surfaceStabilityTimer = null;
let surfaceRecoveryTimes = [];
let updateStatus = { state: updateChecksEnabled ? 'idle' : 'disabled' };
let updateCheckPromise = null;
let manualUpdateDialogRequested = false;
let automaticUpdateCheckScheduled = false;
let automaticUpdateCheckTimer = null;
let notifiedUpdateTag = null;
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

function applyRuntimePortProfile(profile) {
  const ports = profile?.ports;
  if (!Number.isInteger(ports?.engine) || !Number.isInteger(ports?.admin) || !Number.isInteger(ports?.web)) {
    throw new Error('Invalid V8OS runtime port profile.');
  }
  const previousWebBaseUrl = webBaseUrl;
  engineBaseUrl = `http://127.0.0.1:${ports.engine}`;
  adminBaseUrl = `http://127.0.0.1:${ports.admin}`;
  webBaseUrl = `http://127.0.0.1:${ports.web}`;
  process.env.V8_ENGINE_BASE_URL = engineBaseUrl;
  process.env.V8_ADMIN_BASE_URL = adminBaseUrl;
  process.env.V8_WEB_BASE_URL = webBaseUrl;
  productOrigins = trustedProductOrigins([webBaseUrl, adminBaseUrl]);
  if (pendingSurfaceUrl) {
    try {
      const pending = new URL(pendingSurfaceUrl);
      if (pending.origin === new URL(previousWebBaseUrl).origin) {
        pendingSurfaceUrl = new URL(`${pending.pathname}${pending.search}${pending.hash}`, webBaseUrl).toString();
      }
    } catch {}
  }
  return { ...ports };
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
  if (!coreServicesReady) {
    void loadInitialSurface();
    return Promise.resolve(false);
  }
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
  const chatUrl = activeSessionId ? `${webBaseUrl}/chat?id=${encodeURIComponent(activeSessionId)}` : `${webBaseUrl}/chat`;
  return loadInMainWindow(chatUrl);
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

function humanLogRefs(...records) {
  const names = records
    .flatMap((record) => [record?.logErr, record?.logOut])
    .filter(Boolean)
    .map((filePath) => path.posix.basename(String(filePath).replaceAll('\\', '/')))
    .filter((name) => /^(engine|admin|web)\.(out|err)\.log$/.test(name));
  return [...new Set(names)].map((name) => `~/.v8-agent-os/logs/cli/${name}`);
}

function coreServiceStartupError(failures, fallbackStage = 'start_request') {
  const normalized = failures.map((failure) => {
    const id = String(failure?.id || 'unknown');
    const stage = String(failure?.stage || fallbackStage);
    const logs = humanLogRefs(failure);
    const stageLabel = stage === 'post_spawn'
      ? '启动进程 / process startup'
      : stage === 'spawn'
        ? '创建进程 / process creation'
      : stage === 'readiness_process_check'
        ? '进程存活检查 / process liveness check'
        : stage === 'readiness'
          ? '就绪检查 / readiness check'
          : '启动请求 / start request';
    const processStillRunning = failure?.pidAlive === true || failure?.portOpen === true;
    const statusLabel = failure?.status === 'startup_exit'
      ? '启动后立即退出 / exited immediately after launch'
      : failure?.status === 'spawn_failed'
        ? '无法创建进程 / process could not be created'
      : failure?.status === 'readiness_timeout'
        ? processStillRunning
          ? '进程仍在运行，但服务未能及时就绪 / process is running, but the service did not become ready in time'
          : '服务未能及时就绪 / service did not become ready in time'
        : '未能启动 / failed to start';
    return {
      id,
      stage,
      status: String(failure?.status || 'startup_failed'),
      processStillRunning,
      logs,
      message: `${CORE_SERVICE_LABELS[id] || id}: ${statusLabel}; 阶段 / Stage: ${stageLabel}${logs.length ? `; 日志 / Logs: ${logs.join(', ')}` : ''}`,
    };
  });
  const error = new Error(normalized.map((item) => item.message).join('\n'));
  error.userFacingMessage = `核心服务未能完成启动 / Core services could not finish starting.\n${error.message}`;
  error.serviceIds = normalized.map((item) => item.id);
  error.restartServiceIds = normalized
    .filter((item) => item.status === 'readiness_timeout' && item.processStillRunning)
    .map((item) => item.id);
  error.startupStage = normalized[0]?.stage || fallbackStage;
  error.logRefs = [...new Set(normalized.flatMap((item) => item.logs))];
  return error;
}

async function waitForUrl(url, options = {}) {
  const timeoutMs = options.timeoutMs ?? 90000;
  const intervalMs = options.intervalMs ?? 700;
  const startedAt = Date.now();
  let lastError = null;
  let lastReportedReason = null;
  while (!options.isCancelled?.() && Date.now() - startedAt < timeoutMs) {
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
  if (options.isCancelled?.()) return false;
  const timeoutError = new Error(`readiness_timeout${lastError ? `:${lastError.message}` : ''}`);
  timeoutError.serviceId = options.kind || 'unknown';
  timeoutError.startupStage = 'readiness';
  throw timeoutError;
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function monitorCoreServiceLiveness(startResults, isComplete) {
  const startedById = new Map(
    startResults
      .filter((item) => CORE_SERVICE_IDS.includes(item.id) && Number.isInteger(item.pid) && item.pid > 0)
      .map((item) => [item.id, item]),
  );
  await sleep(500);
  while (!isComplete()) {
    const exitedIds = [...startedById]
      .filter(([, started]) => !isProcessAlive(started.pid))
      .map(([id]) => id);
    if (exitedIds.length === 0) {
      await sleep(800);
      continue;
    }
    try {
      const { shellStatus } = await cliApi();
      const statuses = await shellStatus(exitedIds);
      const failures = statuses
        .filter((status) => !status.pidAlive && !status.portOpen)
        .map((status) => {
          const started = startResults.find((item) => item.id === status.id) || {};
          return {
            ...started,
            ...status,
            status: 'startup_exit',
            stage: 'readiness_process_check',
            logErr: status.logErr || started.logErr,
            logOut: status.logOut || started.logOut,
          };
        });
      if (failures.length > 0) throw coreServiceStartupError(failures, 'readiness_process_check');
    } catch (error) {
      if (error?.userFacingMessage) throw error;
      reportSurfaceStage('service_liveness_check_unavailable');
    }
    await sleep(800);
  }
}

async function waitForServices(startResults) {
  let complete = false;
  const readiness = Promise.all([
    waitForUrl(`${engineBaseUrl}/readyz`, { timeoutMs: 120000, kind: 'engine', isCancelled: () => complete })
      .then((ready) => ready && reportSurfaceStage('readiness_probe_ready', { service: 'engine' })),
    waitForUrl(`${adminBaseUrl}/login`, {
      timeoutMs: 120000,
      kind: 'admin',
      expectedOrigin: new URL(adminBaseUrl).origin,
      isCancelled: () => complete,
    })
      .then((ready) => ready && reportSurfaceStage('readiness_probe_ready', { service: 'admin' })),
    waitForUrl(`${webBaseUrl}/chat`, { timeoutMs: 120000, kind: 'web', isCancelled: () => complete })
      .then((ready) => ready && reportSurfaceStage('readiness_probe_ready', { service: 'web' })),
  ]);
  try {
    await Promise.race([
      readiness,
      monitorCoreServiceLiveness(startResults, () => complete),
    ]);
  } catch (error) {
    if (error?.userFacingMessage) throw error;
    let statuses = [];
    try {
      const { shellStatus } = await cliApi();
      statuses = await shellStatus(CORE_SERVICE_IDS);
    } catch {}
    const serviceId = error?.serviceId || 'unknown';
    const started = startResults.find((item) => item.id === serviceId) || { id: serviceId };
    const status = statuses.find((item) => item.id === serviceId) || {};
    throw coreServiceStartupError([{
      ...started,
      ...status,
      id: serviceId,
      status: status.pidAlive === false && !status.portOpen ? 'startup_exit' : 'readiness_timeout',
      stage: 'readiness',
      logErr: status.logErr || started.logErr,
      logOut: status.logOut || started.logOut,
    }], 'readiness');
  } finally {
    complete = true;
  }
}

async function ensureCoreServicesStarted() {
  if (!coreServicesStartPromise) {
    coreServicesStartPromise = cliApi()
      .then(async ({ shellStartWithRuntimePorts }) => {
        const { profile, results } = await shellStartWithRuntimePorts(CORE_SERVICE_IDS, { mode: 'start' });
        applyRuntimePortProfile(profile);
        const failures = results.filter((item) => !['started', 'already_running'].includes(item.status));
        if (failures.length > 0) {
          throw coreServiceStartupError(failures);
        }
        return results;
      });
  }
  const currentAttempt = coreServicesStartPromise;
  try {
    return await currentAttempt;
  } finally {
    if (coreServicesStartPromise === currentAttempt) coreServicesStartPromise = null;
  }
}

async function isInstanceInitialized() {
  try {
    const { response, body } = await fetchTextWithTimeout(
      net.fetch.bind(net),
      `${adminBaseUrl}/api/client/instance`,
      2000,
      {
        cache: 'no-store',
        credentials: 'omit',
      },
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = JSON.parse(body || '{}');
    if (payload?.kind !== 'v8_instance_manifest' || typeof payload?.initialized !== 'boolean') {
      throw new Error('instance manifest contract mismatch');
    }
    return payload.initialized;
  } catch (error) {
    const failure = new Error('无法确认本机初始化状态 / Unable to confirm local initialization state.');
    failure.cause = error;
    throw failure;
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

function startupDataUrl(detail, options = {}) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(buildStartupHtml({
    markUrl: productMarkUrl(),
    detail,
    actionLabel: options.actionLabel || '',
  }))}`;
}

function errorDataUrl(error) {
  const safeReason = typeof error?.userFacingMessage === 'string'
    ? error.userFacingMessage
    : '界面或核心服务未能完成启动 / The product surface or a core service could not finish starting.';
  return startupDataUrl(
    `启动未完成 / Startup incomplete\n${safeReason}\n请重试，或从托盘查看服务状态并运行 v8os doctor。 / Retry, or inspect service status from the tray and run v8os doctor.`,
    { actionLabel: '重试 / Retry' },
  );
}

function reportSurfaceStage(stage, details = {}) {
  console.log('[v8os-shell] surface', JSON.stringify({ stage, ...details }));
}

function isLocalProductSurface(url) {
  return isTrustedProductUrl(url, productOrigins);
}

function isTrustedShellIpc(event, options = {}) {
  const frame = event?.senderFrame;
  const mainFrame = event?.sender?.mainFrame;
  return isTrustedIpcSource({
    senderMatches: Boolean(mainWindow && !mainWindow.isDestroyed() && event?.sender === mainWindow.webContents),
    isMainFrame: Boolean(frame
      && mainFrame
      && frame.processId === mainFrame.processId
      && frame.routingId === mainFrame.routingId),
    frameUrl: frame?.url || '',
    origins: productOrigins,
    allowStartup: options.allowStartup === true,
  });
}

function onTrustedShellIpc(channel, listener, options = {}) {
  ipcMain.on(channel, (event, ...args) => {
    if (!isTrustedShellIpc(event, options)) return;
    listener(event, ...args);
  });
}

function handleTrustedShellIpc(channel, listener, options = {}) {
  ipcMain.handle(channel, async (event, ...args) => {
    if (!isTrustedShellIpc(event, options)) throw new Error('untrusted_ipc_sender');
    return listener(event, ...args);
  });
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
  void mainWindow.loadURL(startupDataUrl('界面进程正在恢复 / Restoring the product surface...')).catch(() => undefined);
  surfaceRecoveryTimer = setTimeout(() => {
    surfaceRecoveryTimer = null;
    if (!quitting && mainWindow && !mainWindow.isDestroyed()) void loadInitialSurface();
  }, 700);
}

async function performInitialSurfaceLoad() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  coreServicesReady = false;
  shellControl?.setSurfaceStatus({ surfaceReady: false });
  reportSurfaceStage('starting_core_services');
  try {
    const startResults = await ensureCoreServicesStarted();
    reportSurfaceStage('waiting_for_readiness');
    await loadUrlSafely(
      () => mainWindow.loadURL(startupDataUrl('核心服务已启动，正在等待界面就绪 / Core services started; waiting for the product surfaces...')),
      (error) => console.error('[v8os-shell] failed to update startup stage', error),
    );
    await waitForServices(startResults);
    lastStartupFailure = null;
    reportSurfaceStage('core_services_ready');
    coreServicesReady = true;
    const defaultChatUrl = activeSessionId ? `${webBaseUrl}/chat?id=${encodeURIComponent(activeSessionId)}` : `${webBaseUrl}/chat`;
    const initialized = await isInstanceInitialized();
    const targetUrl = initialProductSurfaceUrl({
      initialized,
      pendingSurfaceUrl,
      defaultChatUrl,
      adminBaseUrl,
    });
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
    coreServicesReady = false;
    coreServicesStartPromise = null;
    lastStartupFailure = error?.userFacingMessage ? error : null;
    try {
      const { shellStatus } = await cliApi();
      await shellStatus(CORE_SERVICE_IDS);
    } catch {}
    reportSurfaceStage('startup_failed', { reason: error?.message || String(error) });
    shellControl?.setSurfaceStatus({ surfaceReady: false });
    await loadUrlSafely(
      () => mainWindow.loadURL(errorDataUrl(error)),
      (fallbackError) => console.error('[v8os-shell] failed to show startup error surface', fallbackError),
    );
  }
}

async function restartRetryableCoreServices(failure) {
  const serviceIds = [...new Set(
    (Array.isArray(failure?.restartServiceIds) ? failure.restartServiceIds : [])
      .filter((id) => CORE_SERVICE_IDS.includes(id)),
  )];
  if (serviceIds.length === 0) return;
  const { shellStatus, shellStop } = await cliApi();
  await shellStop(serviceIds, { stopVerifiedPortOwners: serviceIds });
  const remaining = await waitForManagedServicesStopped(shellStatus, 25, serviceIds);
  if (remaining.some((item) => item.pidAlive || item.portOpen)) {
    throw coreServiceStartupError(remaining.map((item) => ({
      ...item,
      status: 'retry_stop_failed',
      stage: 'retry_stop',
    })), 'retry_stop');
  }
}

async function retryInitialSurface() {
  if (initialSurfaceLoadPromise) return initialSurfaceLoadPromise;
  const failure = lastStartupFailure;
  try {
    await restartRetryableCoreServices(failure);
    lastStartupFailure = null;
    return await loadInitialSurface();
  } catch (error) {
    lastStartupFailure = error?.userFacingMessage ? error : failure;
    await loadUrlSafely(
      () => mainWindow?.loadURL(errorDataUrl(error)),
      (fallbackError) => console.error('[v8os-shell] failed to show retry error surface', fallbackError),
    );
    return false;
  }
}

function loadInitialSurface() {
  if (!initialSurfaceLoadPromise) {
    initialSurfaceLoadPromise = performInitialSurfaceLoad().finally(() => {
      initialSurfaceLoadPromise = null;
    });
  }
  return initialSurfaceLoadPromise;
}

async function startShellControl() {
  shellControl = createShellControlServer({
    packaged: app.isPackaged,
    executablePath: process.execPath,
    repoRoot,
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

function setUpdateStatus(nextStatus) {
  updateStatus = nextStatus;
  updateTrayMenu();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('v8os-shell:update-status', publicUpdateStatus());
  }
}

function publicUpdateStatus() {
  let currentVersion = null;
  try {
    currentVersion = loadReleaseIdentity(
      releaseManifestPath,
      app.getVersion(),
      process.platform,
      process.arch,
    ).version;
  } catch {}
  const state = ['idle', 'checking', 'current', 'available', 'error', 'disabled'].includes(updateStatus?.state)
    ? updateStatus.state
    : 'error';
  return {
    state,
    currentVersion,
    version: typeof updateStatus?.version === 'string' ? updateStatus.version : null,
    tag: typeof updateStatus?.tag === 'string' ? updateStatus.tag : null,
    releaseUrl: typeof updateStatus?.releaseUrl === 'string' ? updateStatus.releaseUrl : null,
    publishedAt: typeof updateStatus?.publishedAt === 'string' ? updateStatus.publishedAt : null,
    errorCode: typeof updateStatus?.errorCode === 'string' ? updateStatus.errorCode : null,
  };
}

async function openUpdateRelease(result = updateStatus) {
  if (result?.state !== 'available') return false;
  let controlledUrl;
  try {
    controlledUrl = releaseUrlForTag(result.tag);
  } catch {
    return false;
  }
  if (controlledUrl !== result.releaseUrl) return false;
  try {
    await shell.openExternal(controlledUrl);
    return true;
  } catch {
    console.warn('[v8os-shell] controlled release page could not be opened');
    return false;
  }
}

function notifyUpdateAvailable(result) {
  try {
    if (notifiedUpdateTag === result.tag || !Notification.isSupported()) return;
    notifiedUpdateTag = result.tag;
    const notification = new Notification({
      title: 'V8 Agent OS',
      body: `发现新版本 ${result.version} / Update available`,
      silent: true,
    });
    notification.on('click', () => { void openUpdateRelease(result); });
    notification.show();
  } catch {
    console.warn('[v8os-shell] update notification unavailable');
  }
}

async function showUpdateCheckResult(result) {
  const options = result.state === 'available'
    ? {
        type: 'info',
        title: 'V8OS 更新 / Update',
        message: `发现新版本 ${result.version} / Update available`,
        detail: '下载与安装仍由你确认。当前 Preview 不会静默下载或安装。\nDownload and installation require your confirmation.',
        buttons: ['打开下载页 / Open download page', '稍后 / Later'],
        defaultId: 0,
        cancelId: 1,
      }
    : result.state === 'current'
      ? {
          type: 'info',
          title: 'V8OS 更新 / Update',
          message: '当前已是最新版 / V8OS is up to date',
          buttons: ['确定 / OK'],
        }
      : {
          type: 'warning',
          title: 'V8OS 更新 / Update',
          message: '暂时无法检查更新 / Unable to check for updates',
          detail: '请稍后重试。该问题不会影响 V8OS 的本地运行。\nTry again later. Local V8OS operation is unaffected.',
          buttons: ['确定 / OK'],
        };
  try {
    showMainWindow();
    const response = mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible()
      ? await dialog.showMessageBox(mainWindow, options)
      : await dialog.showMessageBox(options);
    if (result.state === 'available' && response.response === 0) await openUpdateRelease(result);
  } catch {
    console.warn('[v8os-shell] update result dialog unavailable');
  }
}

async function performDesktopUpdateCheck() {
  setUpdateStatus({ state: 'checking' });
  try {
    const identity = loadReleaseIdentity(releaseManifestPath, app.getVersion(), process.platform, process.arch);
    const result = await checkForDesktopUpdate({
      fetchImpl: net.fetch.bind(net),
      identity,
    });
    setUpdateStatus(result);
    return result;
  } catch (error) {
    const result = {
      state: 'error',
      errorCode: typeof error?.code === 'string' ? error.code : 'update_check_failed',
    };
    console.warn('[v8os-shell] update check unavailable', { reason: result.errorCode });
    setUpdateStatus(result);
    return result;
  }
}

function requestDesktopUpdateCheck(options = {}) {
  if (!updateChecksEnabled) return Promise.resolve({ state: 'disabled' });
  if (options.manual) manualUpdateDialogRequested = true;
  if (!updateCheckPromise) {
    const automaticRequest = !options.manual && !options.surface;
    updateCheckPromise = performDesktopUpdateCheck()
      .then(async (result) => {
        const showManualDialog = manualUpdateDialogRequested;
        manualUpdateDialogRequested = false;
        if (showManualDialog) {
          await showUpdateCheckResult(result);
        } else if (automaticRequest && result.state === 'available') {
          notifyUpdateAvailable(result);
        }
        return result;
      })
      .finally(() => {
        updateCheckPromise = null;
      });
  }
  return updateCheckPromise;
}

function scheduleAutomaticUpdateCheck() {
  if (!updateChecksEnabled || automaticUpdateCheckScheduled) return;
  automaticUpdateCheckScheduled = true;
  automaticUpdateCheckTimer = setTimeout(() => {
    automaticUpdateCheckTimer = null;
    void requestDesktopUpdateCheck();
  }, AUTOMATIC_UPDATE_CHECK_DELAY_MS);
  automaticUpdateCheckTimer.unref?.();
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

async function waitForManagedServicesStopped(shellStatus, attempts = 15, serviceIds = CORE_SERVICE_IDS) {
  let statuses = [];
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    statuses = await shellStatus(serviceIds);
    if (statuses.every((item) => !item.pidAlive && !item.portOpen)) return statuses;
    await wait(100);
  }
  return statuses;
}

async function runManagedV8OSShutdown({
  coreIds,
  desktopPetId,
  shouldStopDesktopPet,
  stopDesktopPetGracefully,
  shellStop,
  shellStatus,
  waitForServicesStopped,
  removeShellProcessRecord,
  shellProcessRecordIdentity,
  stopControl,
  quitApplication,
  onDesktopPetShutdownError = () => undefined,
  onCoreRetry = () => undefined,
}) {
  const blockersFor = (statuses, serviceIds) => serviceIds.flatMap((id) => {
    const status = Array.isArray(statuses) ? statuses.find((item) => item?.id === id) : null;
    if (!status) return [{ id, state: 'status_missing', pidAlive: null, portOpen: null }];
    return status.pidAlive === false && status.portOpen === false ? [] : [status];
  });
  const stopOptions = { stopVerifiedPortOwners: coreIds };

  let desktopPetShutdownError = null;
  if (shouldStopDesktopPet) {
    try {
      await stopDesktopPetGracefully();
    } catch (error) {
      desktopPetShutdownError = error;
      onDesktopPetShutdownError(error);
    }
  }

  const firstStop = await shellStop(coreIds, stopOptions);
  let coreStatuses = await waitForServicesStopped(shellStatus, 15, coreIds);
  let coreBlockers = blockersFor(coreStatuses, coreIds);
  if (coreBlockers.length > 0) {
    const retryStop = await shellStop(coreIds, stopOptions);
    coreStatuses = await waitForServicesStopped(shellStatus, 10, coreIds);
    coreBlockers = blockersFor(coreStatuses, coreIds);
    onCoreRetry({ firstStop, retryStop, remaining: coreBlockers });
  }
  if (coreBlockers.length > 0) {
    return { ok: false, reason: 'core_services_still_running', remaining: coreBlockers };
  }

  let desktopPetStatuses = await waitForServicesStopped(shellStatus, 15, [desktopPetId]);
  let desktopPetBlockers = blockersFor(desktopPetStatuses, [desktopPetId]);
  if (desktopPetShutdownError || desktopPetBlockers.length > 0) {
    await shellStop([desktopPetId]);
    desktopPetStatuses = await waitForServicesStopped(shellStatus, 10, [desktopPetId]);
    desktopPetBlockers = blockersFor(desktopPetStatuses, [desktopPetId]);
  }
  if (desktopPetBlockers.length > 0) {
    return { ok: false, reason: 'desktop_pet_still_running', remaining: desktopPetBlockers };
  }

  const allServiceIds = [...coreIds, desktopPetId];
  const finalStatuses = await waitForServicesStopped(shellStatus, 3, allServiceIds);
  const finalBlockers = blockersFor(finalStatuses, allServiceIds);
  if (finalBlockers.length > 0) {
    return { ok: false, reason: 'services_still_running', remaining: finalBlockers };
  }

  await removeShellProcessRecord(shellProcessRecordIdentity);
  await stopControl();
  quitApplication();
  return { ok: true, reason: 'stopped' };
}

function shutdownServiceLabel(id) {
  if (id === 'desktop-pet') return '桌宠 / Desktop Pet';
  return CORE_SERVICE_LABELS[id] || id;
}

async function showShutdownFailure(result) {
  const serviceLabels = [...new Set(
    (Array.isArray(result?.remaining) ? result.remaining : [])
      .map((item) => shutdownServiceLabel(item?.id))
      .filter(Boolean),
  )];
  const detail = [
    'V8OS 已保留 Shell 控制通道和进程所有权，未执行不完整退出。',
    'V8OS kept the Shell control channel and process ownership; no partial exit was performed.',
    serviceLabels.length > 0
      ? `仍在运行或状态不可确认 / Still running or unverified: ${serviceLabels.join(', ')}`
      : '服务状态暂时无法确认，请稍后重试。 / Service status is temporarily unavailable. Try again shortly.',
  ].join('\n');
  showMainWindow();
  const options = {
    type: 'warning',
    title: '无法完全退出 V8OS / Unable to fully quit V8OS',
    message: '部分本地服务尚未确认停止 / Some local services have not been confirmed stopped',
    detail,
    buttons: ['重试退出 / Retry quit', '保留运行 / Keep running'],
    defaultId: 0,
    cancelId: 1,
  };
  const response = mainWindow && !mainWindow.isDestroyed()
    ? await dialog.showMessageBox(mainWindow, options)
    : await dialog.showMessageBox(options);
  return response.response === 0;
}

async function quitV8OS() {
  if (quitting) return;
  quitting = true;
  let failure = null;
  try {
    const { removeShellProcessRecord, shellStatus, shellStop } = await cliApi();
    const result = await runManagedV8OSShutdown({
      coreIds: CORE_SERVICE_IDS,
      desktopPetId: 'desktop-pet',
      shouldStopDesktopPet: desktopPetProcessRunning || Boolean(shellControl?.hasAuthenticatedClient()),
      stopDesktopPetGracefully,
      shellStop,
      shellStatus,
      waitForServicesStopped: waitForManagedServicesStopped,
      removeShellProcessRecord,
      shellProcessRecordIdentity,
      stopControl: async () => {
        await shellControl?.stop();
      },
      quitApplication: () => app.quit(),
      onDesktopPetShutdownError: (error) => {
        console.error('[V8OS Shell] Desktop pet graceful shutdown failed; verifying CLI fallback', {
          reason: error?.message || 'unknown_error',
        });
      },
      onCoreRetry: ({ firstStop, retryStop, remaining }) => {
        console.warn('[V8OS Shell] Core shutdown required reconciliation', { firstStop, retryStop, remaining });
      },
    });
    if (!result.ok) {
      failure = result;
      console.error('[V8OS Shell] Managed services remain after shutdown', {
        reason: result.reason,
        remaining: result.remaining,
      });
    }
  } catch (error) {
    failure = { ok: false, reason: 'shutdown_probe_failed', remaining: [] };
    console.error('[V8OS Shell] Core shutdown phase failed', { reason: error?.message || 'unknown_error' });
  }
  if (!failure) return;

  quitting = false;
  let retry = false;
  try {
    retry = await showShutdownFailure(failure);
  } catch (error) {
    console.warn('[V8OS Shell] Shutdown failure dialog unavailable', { reason: error?.message || 'unknown_error' });
  }
  if (retry) setImmediate(() => { void quitV8OS(); });
}

function updateTrayMenu() {
  if (!tray) return;
  const model = buildTrayMenuModel({ desktopPetState, desktopPetProcessRunning, updateStatus });
  const template = model.map((item) => {
    if (item.type === 'separator') return { type: 'separator' };
    if (item.id === 'open-web') return { label: item.label, click: openWeb };
    if (item.id === 'open-admin') return { label: item.label, click: openAdmin };
    if (item.id === 'start-desktop-pet' || item.id === 'stop-desktop-pet') return { label: item.label, enabled: item.enabled !== false, click: () => { void toggleDesktopPet(); } };
    if (item.id === 'check-update') return { label: item.label, click: () => { void requestDesktopUpdateCheck({ manual: true }); } };
    if (item.id === 'open-update-release') return { label: item.label, click: () => { void openUpdateRelease(); } };
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
      sandbox: true,
    },
  });
  mainWindow.once('ready-to-show', () => {
    showMainWindow();
  });
  mainWindow.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    mainWindow.hide();
  });
  mainWindow.on('maximize', emitWindowState);
  mainWindow.on('unmaximize', emitWindowState);
  mainWindow.on('restore', emitWindowState);
  mainWindow.webContents.on('will-navigate', (event, targetUrl, _isInPlace, isMainFrame) => {
    const mainFrameNavigation = typeof event.isMainFrame === 'boolean' ? event.isMainFrame : isMainFrame;
    const destination = event.url || targetUrl;
    if (mainFrameNavigation !== false && !isLocalProductSurface(destination)) event.preventDefault();
  });
  mainWindow.webContents.on('will-redirect', (event, targetUrl, _isInPlace, isMainFrame) => {
    const mainFrameNavigation = typeof event.isMainFrame === 'boolean' ? event.isMainFrame : isMainFrame;
    const destination = event.url || targetUrl;
    if (mainFrameNavigation !== false && !isLocalProductSurface(destination)) event.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    const route = classifyWindowOpen(url, productOrigins);
    if (route === 'product') {
      void loadInMainWindow(url);
    } else if (route === 'external') {
      void shell.openExternal(url).catch(() => {
        console.warn('[v8os-shell] external page could not be opened');
      });
    }
    return { action: 'deny' };
  });
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
    void waitForProductSurfaceDom(
      (script) => contents.executeJavaScript(script, true),
      surfaceKind,
      {
        timeoutMs: 5000,
        intervalMs: 100,
        isCancelled: () => contents.isDestroyed()
          || mainWindow?.webContents !== contents
          || contents.getURL() !== loadedUrl,
      },
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
      scheduleAutomaticUpdateCheck();
      if (surfaceStabilityTimer) clearTimeout(surfaceStabilityTimer);
      surfaceStabilityTimer = setTimeout(() => {
        surfaceRecoveryTimes = [];
        surfaceStabilityTimer = null;
      }, 15_000);
    });
  });
  void loadUrlSafely(
    () => mainWindow.loadURL(startupDataUrl('正在启动 Engine / Admin / Web... / Starting Engine, Admin, and Web...')),
    (error) => console.error('[v8os-shell] failed to show startup surface', error),
  ).finally(() => {
    void loadInitialSurface();
  });
}

onTrustedShellIpc('v8os-shell:minimize', () => {
  mainWindow?.minimize();
}, { allowStartup: true });

onTrustedShellIpc('v8os-shell:toggle-maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
  emitWindowState();
}, { allowStartup: true });

handleTrustedShellIpc('v8os-shell:get-window-state', () => currentWindowState(), { allowStartup: true });

onTrustedShellIpc('v8os-shell:close', () => {
  mainWindow?.hide();
}, { allowStartup: true });

onTrustedShellIpc('v8os-shell:open-web', () => {
  void openWeb();
});

onTrustedShellIpc('v8os-shell:retry-startup', () => {
  void retryInitialSurface();
}, { allowStartup: true });

onTrustedShellIpc('v8os-shell:open-admin', () => {
  void openAdmin();
});

onTrustedShellIpc('v8os-shell:active-session', (_event, sessionId) => {
  reportActiveSession(sessionId);
});

handleTrustedShellIpc('v8os-shell:open-workspace-folder', async (_event, workspacePath) => {
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

handleTrustedShellIpc('v8os-shell:reveal-workspace-file', async (_event, workspaceRelativePath, workspacePath) => {
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

handleTrustedShellIpc('v8os-shell:select-godot-executable', async () => {
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

handleTrustedShellIpc('v8os-shell:select-godot-project-directory', async () => {
  if (!mainWindow) return { ok: false, error: 'shell_window_unavailable' };
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select Godot project',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) return { ok: false, cancelled: true };
  return { ok: true, path: path.resolve(result.filePaths[0]) };
});

handleTrustedShellIpc('v8os-shell:get-desktop-pet-state', async () => {
  await refreshStatus();
  return currentDesktopPetStatus();
});

handleTrustedShellIpc('v8os-shell:set-desktop-pet-enabled', async (_event, enabled) => {
  await refreshStatus();
  return setDesktopPetEnabled(Boolean(enabled));
});

handleTrustedShellIpc('v8os-shell:get-update-status', async () => publicUpdateStatus());

handleTrustedShellIpc('v8os-shell:check-for-updates', async () => {
  await requestDesktopUpdateCheck({ surface: true });
  return publicUpdateStatus();
});

handleTrustedShellIpc('v8os-shell:open-update-release', async () => openUpdateRelease());

function registerShellProtocol() {
  if (process.defaultApp && process.argv.length >= 2) {
    return app.setAsDefaultProtocolClient('v8os', process.execPath, [path.resolve(process.argv[1])]);
  }
  return app.setAsDefaultProtocolClient('v8os');
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else if (process.argv.includes(MANAGED_SHELL_SHUTDOWN_ARG)) {
  // A governed shutdown request must never become a visible replacement Shell
  // if the original instance exits between CLI identity verification and spawn.
  app.whenReady().then(() => app.exit(0));
} else {
  app.on('second-instance', (_event, argv) => {
    if (argv.includes(MANAGED_SHELL_SHUTDOWN_ARG)) {
      void quitV8OS();
      return;
    }
    const deepLink = deepLinkFromArgv(argv);
    if (!handleShellDeepLink(deepLink)) showMainWindow();
  });
  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault();
    void quitV8OS();
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
  if (automaticUpdateCheckTimer) clearTimeout(automaticUpdateCheckTimer);
  desktopPetShutdown.cancelAll();
  void shellControl?.stop();
});

app.on('window-all-closed', () => {
  // Keep the product shell resident in the tray.
});
