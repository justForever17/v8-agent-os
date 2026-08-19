const {
  desktopPetAvailability,
  LINUX_DESKTOP_PET_UNAVAILABLE_REASON,
} = require('../../v8-agent-os-cli/src/desktop_pet_platform.cjs');

const platformAvailability = desktopPetAvailability();
if (!platformAvailability.available) {
  const error = new Error(platformAvailability.message || LINUX_DESKTOP_PET_UNAVAILABLE_REASON);
  error.code = platformAvailability.reasonCode || LINUX_DESKTOP_PET_UNAVAILABLE_REASON;
  throw error;
}

const { app, BrowserWindow, Menu, Tray, globalShortcut, ipcMain, nativeImage, net, protocol, shell, session, screen, systemPreferences, desktopCapturer } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { createCanonicalConfigWatcher } = require('../lib/canonical-config-watcher.cjs');
const { createShellControlClient } = require('../lib/shell-control-client.cjs');
const { createShellLifecycleWatchdog } = require('../lib/shell-lifecycle-watchdog.cjs');
const { initialSafeShape, normalizeInteractionRegions } = require('../lib/interaction-region-policy.cjs');
const {
  STABLE_RENDERER_ENTRY_URL,
  createDevelopmentTransport,
  createVerifiedTransport,
  developmentRendererContentSecurityPolicy,
  installStableRendererProtocol,
  isTrustedRendererUrl,
  registerStableRendererScheme,
  rendererTransportView,
} = require('../lib/stable-renderer-transport.cjs');

registerStableRendererScheme(protocol);

let mainWindow = null;
let tray = null;
let clickThrough = false;
let bundledServer = null;
let bundledServerReadyPromise = null;
let verifiedLocalTransport = null;
let creatingMainWindow = false;
let panelOpen = false;
let companionClosedSize = { width: 380, height: 380 };
let shuttingDown = false;
let preExpansionBounds = null;
let shellControlClient = null;
let shellLifecycleWatchdog = null;
let canonicalConfigWatcher = null;
let lastShellActiveSessionId = '';
let lastPetStatus = { state: 'waiting_v8os', activeSessionId: null };
let shutdownTimer = null;
let shutdownRequestId = '';
let gpuRecoveryRelaunchArgs = null;
let rendererReadyToShow = false;
let interactionRegionReady = process.platform !== 'win32';
let interactionRegionTimer = null;

const V8_WEB_URL = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
const MANAGED_BY_SHELL = process.env.V8_DESKTOP_PET_MANAGED_BY_SHELL === '1';
const SHELL_SETTINGS_DEEP_LINK = 'v8os://open/admin/desktop-pet';
const DEVELOPMENT_TRANSPORT = process.env.V8_DESKTOP_DEV_SERVER
  ? createDevelopmentTransport(process.env.V8_DESKTOP_DEV_SERVER)
  : null;
const configuredLocalServerPort = process.env.V8_DESKTOP_PORT;
const REQUESTED_LOCAL_SERVER_PORT = configuredLocalServerPort === undefined
  ? 0
  : Number(configuredLocalServerPort);

if (
  !Number.isInteger(REQUESTED_LOCAL_SERVER_PORT)
  || REQUESTED_LOCAL_SERVER_PORT < 0
  || REQUESTED_LOCAL_SERVER_PORT > 65535
) {
  throw new Error(`Invalid desktop pet server port: ${configuredLocalServerPort || ''}`);
}
const CLOSED_WIDTH = 380;
const CLOSED_HEIGHT = 380;
const PANEL_WIDTH = 940;
const PANEL_HEIGHT = 720;
const DESKTOP_PET_DESCRIPTOR_ID = crypto.randomUUID();
const DESKTOP_PET_STARTED_AT = new Date().toISOString();

function desktopPetProcessPath() {
  const stateRoot = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os');
  return path.join(stateRoot, 'runtime', 'desktop-pet.json');
}

function writeDesktopPetProcessDescriptor(transport) {
  const filePath = desktopPetProcessPath();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.tmp`;
  const descriptor = {
    version: 2,
    descriptorId: DESKTOP_PET_DESCRIPTOR_ID,
    pid: process.pid,
    managedByShell: MANAGED_BY_SHELL,
    startedAt: DESKTOP_PET_STARTED_AT,
    ...(app.isPackaged ? {
      packaged: true,
      runtimeKind: 'desktop-pet',
      executablePath: path.resolve(process.execPath),
      repoRoot: path.resolve(String(process.env.V8_REPO_ROOT || '')),
    } : {}),
  };
  if (transport?.mode === 'production') {
    descriptor.serverPid = transport.serverPid;
    descriptor.localPort = transport.localPort;
    descriptor.localBaseUrl = transport.localBaseUrl;
    descriptor.instanceId = transport.instanceId;
  } else if (transport?.mode === 'development') {
    descriptor.devOrigin = transport.rendererOrigin;
  }
  fs.writeFileSync(temporaryPath, JSON.stringify(descriptor, null, 2), 'utf8');
  try {
    fs.renameSync(temporaryPath, filePath);
  } catch {
    fs.rmSync(filePath, { force: true });
    fs.renameSync(temporaryPath, filePath);
  }
}

function removeOwnedDesktopPetProcessDescriptor() {
  const filePath = desktopPetProcessPath();
  try {
    const current = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (
      current?.pid === process.pid
      && current?.descriptorId === DESKTOP_PET_DESCRIPTOR_ID
    ) {
      fs.rmSync(filePath, { force: true });
    }
  } catch {}
}

function getLocalConfigPath() {
  return path.join(app.getPath('userData'), 'cybercore-local-config.json');
}

function readLocalConfigFile() {
  try {
    const raw = fs.readFileSync(getLocalConfigPath(), 'utf8');
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function writeLocalConfigFile(nextConfig) {
  const configPath = getLocalConfigPath();
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  fs.writeFileSync(configPath, JSON.stringify(nextConfig, null, 2), 'utf8');
}

function createTrayIcon() {
  const iconPath = path.join(__dirname, 'assets', 'tray-icon.png');
  const fileIcon = nativeImage.createFromPath(iconPath);
  if (!fileIcon.isEmpty()) {
    return fileIcon;
  }
  const svg = encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
      <defs>
        <linearGradient id="g" x1="8" y1="8" x2="56" y2="56" gradientUnits="userSpaceOnUse">
          <stop stop-color="#ff8a3d"/>
          <stop offset=".48" stop-color="#d946ef"/>
          <stop offset="1" stop-color="#22d3ee"/>
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="18" fill="#0f172a"/>
      <circle cx="32" cy="32" r="24" fill="url(#g)"/>
      <path d="M32 14l4.8 12.2L50 31.8l-13.2 5.5L32 50l-4.8-12.7L14 31.8l13.2-5.6L32 14z" fill="white"/>
    </svg>
  `);
  return nativeImage.createFromDataURL(`data:image/svg+xml;charset=utf-8,${svg}`);
}

async function verifyBundledServer(baseUrl, instanceId, pid, port) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetch(`${baseUrl}/api/pet/health`, {
      cache: 'no-store',
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (
      !response.ok
      || payload?.ok !== true
      || payload?.service !== 'v8-agent-os-desktop-pet'
      || payload?.version !== 1
      || payload?.instanceId !== instanceId
      || payload?.pid !== pid
      || payload?.port !== port
    ) {
      throw new Error('桌宠本地服务身份校验失败');
    }
  } finally {
    clearTimeout(timeout);
  }
}

function startBundledServer() {
  if (bundledServerReadyPromise) return bundledServerReadyPromise;

  const serverPath = path.join(__dirname, '..', 'dist', 'server.cjs');
  const instanceId = crypto.randomUUID();
  const serverRuntimeIsElectron = process.env.V8_DESKTOP_NODE_IS_ELECTRON === '1';
  const serverEnv = {
    ...process.env,
    NODE_ENV: 'production',
    V8_DESKTOP_PORT: String(REQUESTED_LOCAL_SERVER_PORT),
    V8_DESKTOP_SERVER_INSTANCE_ID: instanceId,
  };
  if (serverRuntimeIsElectron) {
    serverEnv.ELECTRON_RUN_AS_NODE = '1';
  } else {
    delete serverEnv.ELECTRON_RUN_AS_NODE;
  }
  const child = spawn(process.env.V8_DESKTOP_NODE || 'node', [serverPath], {
    cwd: path.join(__dirname, '..'),
    env: serverEnv,
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    windowsHide: true,
  });
  bundledServer = child;
  child.stdout.on('data', (chunk) => {
    console.log(`[CyberCore Server] ${String(chunk).trim()}`);
  });
  child.stderr.on('data', (chunk) => {
    console.warn(`[CyberCore Server] ${String(chunk).trim()}`);
  });
  bundledServerReadyPromise = new Promise((resolve, reject) => {
    let startupSettled = false;
    let ready = false;
    let validating = false;
    const startupTimer = setTimeout(() => {
      if (startupSettled) return;
      startupSettled = true;
      bundledServerReadyPromise = null;
      reject(new Error('桌宠本地服务启动超时'));
    }, 8000);

    const rejectStartup = (error) => {
      if (startupSettled) return;
      startupSettled = true;
      clearTimeout(startupTimer);
      bundledServerReadyPromise = null;
      reject(error instanceof Error ? error : new Error(String(error || '桌宠本地服务启动失败')));
    };

    child.on('message', async (message) => {
      if (startupSettled || validating || message?.type !== 'v8-desktop-server-ready') return;
      const port = Number(message.port);
      if (
        message.version !== 1
        || message.instanceId !== instanceId
        || message.pid !== child.pid
        || !Number.isInteger(port)
        || port < 1
        || port > 65535
        || (REQUESTED_LOCAL_SERVER_PORT !== 0 && port !== REQUESTED_LOCAL_SERVER_PORT)
      ) {
        rejectStartup(new Error('桌宠本地服务握手无效'));
        return;
      }
      validating = true;
      const baseUrl = `http://127.0.0.1:${port}`;
      try {
        await verifyBundledServer(baseUrl, instanceId, child.pid, port);
        if (startupSettled || bundledServer !== child) return;
        const transport = createVerifiedTransport({
          baseUrl,
          instanceId,
          serverPid: child.pid,
        });
        verifiedLocalTransport = transport;
        writeDesktopPetProcessDescriptor(transport);
        startupSettled = true;
        ready = true;
        clearTimeout(startupTimer);
        resolve(transport);
      } catch (error) {
        rejectStartup(error);
      } finally {
        validating = false;
      }
    });
    child.on('error', (error) => {
      if (!ready) {
        rejectStartup(error);
        return;
      }
      console.error('[CyberCore Server] process error', error);
      if (!shuttingDown) {
        reportPetStatus('error');
        emergencyHideWindow();
        safeShutdown({ source: 'local_server_process_error' });
      }
    });
    child.on('exit', (code, signal) => {
      console.warn('[CyberCore Server] exited', { code, signal });
      if (bundledServer === child) bundledServer = null;
      if (verifiedLocalTransport?.serverPid === child.pid) verifiedLocalTransport = null;
      bundledServerReadyPromise = null;
      if (!startupSettled) {
        rejectStartup(new Error(`桌宠本地服务提前退出（${code ?? signal ?? 'unknown'}）`));
      } else if (ready && !shuttingDown) {
        reportPetStatus('error');
        emergencyHideWindow();
        safeShutdown({ source: 'local_server_exited' });
      }
    });
  });

  return bundledServerReadyPromise;
}

function killBundledServerTree() {
  const child = bundledServer;
  bundledServerReadyPromise = null;
  if (!child) return;
  bundledServer = null;
  if (verifiedLocalTransport?.serverPid === child.pid) verifiedLocalTransport = null;
  const pid = child.pid;
  try {
    child.removeAllListeners();
  } catch {
    // best-effort cleanup
  }
  if (process.platform === 'win32' && pid) {
    try {
      spawn('taskkill', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
      return;
    } catch {
      // fall back to normal child kill below
    }
  }
  try {
    child.kill();
  } catch {
    // process may already be gone
  }
}

function sendPrepareShutdown(requestId) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.webContents.send('v8-desktop:prepare-shutdown', { requestId });
  } catch {
    // renderer may already be gone
  }
}

function neutralizeDesktopOverlay() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    if (process.platform === 'win32') mainWindow.setShape(initialSafeShape(mainWindow.getBounds()));
    mainWindow.setIgnoreMouseEvents(true, { forward: false });
  } catch {}
  try {
    mainWindow.setAlwaysOnTop(false);
  } catch {}
  try {
    mainWindow.setSkipTaskbar(true);
  } catch {}
}

function emergencyHideWindow() {
  clickThrough = true;
  neutralizeDesktopOverlay();
  try {
    mainWindow?.hide();
  } catch {}
  updateTrayMenu();
}

function openLocalProduct(baseUrl, targetPath = '') {
  const base = String(baseUrl || '').replace(/\/+$/, '');
  const pathPart = targetPath ? `/${String(targetPath).replace(/^\/+/, '')}` : '';
  void shell.openExternal(`${base}${pathPart}`);
}

function sendToRenderer(channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isLoading()) return false;
  try {
    mainWindow.webContents.send(channel, payload);
    return true;
  } catch {
    return false;
  }
}

function reportPetStatus(state, activeSessionId = lastPetStatus.activeSessionId) {
  lastPetStatus = {
    state,
    activeSessionId: activeSessionId || null,
  };
  return shellControlClient?.send('pet-status', lastPetStatus) || false;
}

function startShellControlClient() {
  if (shellControlClient) return;
  if (MANAGED_BY_SHELL && !shellLifecycleWatchdog) {
    shellLifecycleWatchdog = createShellLifecycleWatchdog({
      isControlConnected: () => Boolean(shellControlClient?.isConnected()),
      onShellUnavailable(event) {
        if (shuttingDown) return;
        console.warn('[CyberCore Desktop] Shell process unavailable; closing managed desktop pet', event);
        safeShutdown({ source: event.reason });
      },
    });
  }
  shellControlClient = createShellControlClient({
    onConnected() {
      shellLifecycleWatchdog?.markConnected();
      reportPetStatus(lastPetStatus.state, lastPetStatus.activeSessionId);
      sendToRenderer('v8-desktop:shell-active-session', { sessionId: lastShellActiveSessionId || null });
    },
    onDisconnected() {
      shellLifecycleWatchdog?.markDisconnected();
    },
    onMessage(message) {
      if (message.type === 'active-session') {
        lastShellActiveSessionId = String(message.sessionId || '');
        sendToRenderer('v8-desktop:shell-active-session', { sessionId: lastShellActiveSessionId || null });
        return;
      }
      if (message.type === 'shutdown') {
        safeShutdown({ source: 'shell', requestId: message.requestId });
      }
    },
  });
  shellControlClient.start();
  shellLifecycleWatchdog?.markDisconnected();
}

function startCanonicalConfigWatcher() {
  if (canonicalConfigWatcher) return;
  canonicalConfigWatcher = createCanonicalConfigWatcher({
    onChange(payload) {
      sendToRenderer('v8-desktop:config-changed', payload);
    },
  });
  canonicalConfigWatcher.start();
}

function requestDesktopPetSettings() {
  if (shellControlClient?.send('open-settings')) return true;
  void shell.openExternal(SHELL_SETTINGS_DEEP_LINK);
  return true;
}

function requestOpenSession(sessionId) {
  return shellControlClient?.send('open-session', { sessionId: String(sessionId || '').trim() }) || false;
}

function finalizeShutdown(reason = 'renderer_ready') {
  if (!shuttingDown) return false;
  if (shutdownTimer) clearTimeout(shutdownTimer);
  shutdownTimer = null;
  killBundledServerTree();
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.hide();
      mainWindow.destroy();
    }
  } catch {}
  mainWindow = null;
  try {
    tray?.destroy();
  } catch {}
  tray = null;
  shellControlClient?.stop();
  shellControlClient = null;
  removeOwnedDesktopPetProcessDescriptor();
  const relaunchArgs = gpuRecoveryRelaunchArgs;
  gpuRecoveryRelaunchArgs = null;
  if (relaunchArgs) app.relaunch({ args: relaunchArgs });
  app.exit(0);
  return true;
}

function safeShutdown(options = {}) {
  if (shuttingDown) return true;
  shuttingDown = true;
  app.emit('v8os-governed-shutdown-started');
  shellLifecycleWatchdog?.stop();
  shellLifecycleWatchdog = null;
  shutdownRequestId = String(options.requestId || `pet-${Date.now()}`);
  reportPetStatus('stopping');
  sendPrepareShutdown(shutdownRequestId);
  neutralizeDesktopOverlay();
  try {
    globalShortcut.unregisterAll();
  } catch {}
  shutdownTimer = setTimeout(() => finalizeShutdown('renderer_timeout'), 1500);
  return true;
}

function setupPermissionHandlers() {
  const allowMediaForLocalApp = (webContents, requestingUrl = '') => {
    const rendererUrl = webContents?.getURL?.() || '';
    if (!isTrustedRendererUrl(rendererUrl, DEVELOPMENT_TRANSPORT)) return false;
    return !requestingUrl || isTrustedRendererUrl(requestingUrl, DEVELOPMENT_TRANSPORT);
  };

  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const isAllowedPermission = 
      permission === 'media' || 
      permission === 'camera' || 
      permission === 'microphone' || 
      permission === 'speaker-selection' ||
      permission === 'audioCapture' ||
      permission === 'videoCapture';
      
    const requestingUrl = details?.requestingUrl || details?.securityOrigin || '';
    if (isAllowedPermission && allowMediaForLocalApp(webContents, requestingUrl)) {
      const requested = details?.mediaTypes || [];
      callback(!requested.length || requested.includes('video') || requested.includes('audio'));
      return;
    }
    callback(false);
  });

  session.defaultSession.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
    const isAllowedPermission = 
      permission === 'media' || 
      permission === 'camera' || 
      permission === 'microphone' || 
      permission === 'speaker-selection' ||
      permission === 'audioCapture' ||
      permission === 'videoCapture';

    if (isAllowedPermission) {
      return allowMediaForLocalApp(webContents, requestingOrigin);
    }
    return false;
  });

  session.defaultSession.setDisplayMediaRequestHandler((request, callback) => {
    if (allowMediaForLocalApp(request.webContents)) {
      desktopCapturer.getSources({ types: ['screen', 'window'] }).then((sources) => {
        const primary = sources.find(s => s.name.toLowerCase().includes('screen') || s.id.startsWith('screen:')) || sources[0];
        if (primary) {
          callback({ video: primary });
        } else {
          callback({ error: 'No screen sources available' });
        }
      }).catch((err) => {
        callback({ error: String(err.message || err) });
      });
    } else {
      callback({ error: 'Not allowed' });
    }
  });
}

function getMediaAccessStatus(kind) {
  if (process.platform === 'darwin' && typeof systemPreferences.getMediaAccessStatus === 'function') {
    try {
      return systemPreferences.getMediaAccessStatus(kind === 'camera' ? 'camera' : 'microphone');
    } catch {
      return 'unknown';
    }
  }
  return 'app_controlled';
}

async function requestMediaAccess(kind) {
  if (process.platform === 'darwin' && typeof systemPreferences.askForMediaAccess === 'function') {
    try {
      return await systemPreferences.askForMediaAccess(kind === 'camera' ? 'camera' : 'microphone');
    } catch {
      return false;
    }
  }
  return true;
}

function openMediaPrivacySettings(kind) {
  if (process.platform === 'win32') {
    void shell.openExternal(kind === 'camera' ? 'ms-settings:privacy-webcam' : 'ms-settings:privacy-microphone');
    return true;
  }
  if (process.platform === 'darwin') {
    const target = kind === 'camera' ? 'Privacy_Camera' : 'Privacy_Microphone';
    void shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?${target}`);
    return true;
  }
  return false;
}

async function resolveEntry() {
  if (DEVELOPMENT_TRANSPORT) {
    writeDesktopPetProcessDescriptor(DEVELOPMENT_TRANSPORT);
    return { type: 'url', value: DEVELOPMENT_TRANSPORT.entryUrl };
  }
  await startBundledServer();
  return { type: 'url', value: STABLE_RENDERER_ENTRY_URL };
}

function setupDevelopmentContentSecurityPolicy() {
  if (!DEVELOPMENT_TRANSPORT) return;
  const contentSecurityPolicy = developmentRendererContentSecurityPolicy(DEVELOPMENT_TRANSPORT);
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    if (!isTrustedRendererUrl(details.url, DEVELOPMENT_TRANSPORT)) {
      callback({ responseHeaders: details.responseHeaders });
      return;
    }
    const responseHeaders = { ...(details.responseHeaders || {}) };
    for (const name of Object.keys(responseHeaders)) {
      if (name.toLowerCase() === 'content-security-policy' || name.toLowerCase() === 'content-security-policy-report-only') {
        delete responseHeaders[name];
      }
    }
    responseHeaders['Content-Security-Policy'] = [contentSecurityPolicy];
    callback({ responseHeaders });
  });
}

async function createMainWindow() {
  if (creatingMainWindow || (mainWindow && !mainWindow.isDestroyed())) return;
  creatingMainWindow = true;
  try {
    await createMainWindowInternal();
  } finally {
    creatingMainWindow = false;
  }
}

function maybeShowMainWindow() {
  if (!rendererReadyToShow || !interactionRegionReady || !mainWindow || mainWindow.isDestroyed() || shuttingDown) return false;
  if (interactionRegionTimer) clearTimeout(interactionRegionTimer);
  interactionRegionTimer = null;
  mainWindow.show();
  updateTrayMenu();
  return true;
}

function armInteractionRegionDeadline() {
  if (process.platform !== 'win32' || interactionRegionReady || interactionRegionTimer) return;
  interactionRegionTimer = setTimeout(() => {
    interactionRegionTimer = null;
    if (interactionRegionReady || shuttingDown) return;
    console.error('[CyberCore Desktop] renderer did not publish a bounded Windows interaction region');
    reportPetStatus('error');
    emergencyHideWindow();
    safeShutdown({ source: 'interaction_region_unavailable' });
  }, 5_000);
}

async function createMainWindowInternal() {
  const entry = await resolveEntry();
  if (shuttingDown) return;
  const trayIcon = createTrayIcon();
  const primaryDisplay = screen.getPrimaryDisplay();
  const { x, y, width, height } = primaryDisplay.workArea;
  rendererReadyToShow = false;
  interactionRegionReady = process.platform !== 'win32';

  mainWindow = new BrowserWindow({
    x,
    y,
    width,
    height,
    frame: false,
    roundedCorners: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    focusable: false,
    icon: trayIcon,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.platform === 'win32') {
    mainWindow.setShape(initialSafeShape({ width, height }));
    mainWindow.setIgnoreMouseEvents(false);
    clickThrough = false;
  } else {
    mainWindow.setIgnoreMouseEvents(true, { forward: true });
    clickThrough = true;
  }

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3 || shuttingDown) return;
    console.error('[CyberCore Desktop] load failed:', errorCode, errorDescription, validatedURL);
    reportPetStatus('error');
    emergencyHideWindow();
    safeShutdown({ source: 'renderer_load_failed' });
  });

  mainWindow.webContents.on('console-message', (_event, level, message) => {
    const prefix = level >= 2 ? 'warn' : 'log';
    console[prefix]('[CyberCore Desktop renderer]', message);
  });

  mainWindow.webContents.on('did-finish-load', () => {
    sendToRenderer('v8-desktop:shell-active-session', { sessionId: lastShellActiveSessionId || null });
  });

  mainWindow.once('ready-to-show', () => {
    rendererReadyToShow = true;
    if (!maybeShowMainWindow()) armInteractionRegionDeadline();
  });

  mainWindow.on('close', (event) => {
    if (shuttingDown) return;
    event.preventDefault();
    safeShutdown({ source: 'window' });
  });

  mainWindow.on('closed', () => {
    if (interactionRegionTimer) clearTimeout(interactionRegionTimer);
    interactionRegionTimer = null;
    rendererReadyToShow = false;
    interactionRegionReady = process.platform !== 'win32';
    mainWindow = null;
  });

  await mainWindow.loadURL(entry.value);
}

function resizeForPanel(open) {
  panelOpen = Boolean(open);
  return null;
}

function showOrFocusWindow() {
  if (!mainWindow) {
    if (!shuttingDown) void createMainWindow().catch(handleMainWindowStartupFailure);
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function createTray() {
  if (MANAGED_BY_SHELL) return;
  tray = new Tray(createTrayIcon());
  tray.setToolTip('V8OS 桌宠');
  updateTrayMenu();
  tray.on('click', showOrFocusWindow);
}

function updateTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '打开 V8OS', click: () => openLocalProduct(V8_WEB_URL, '/chat') },
      { label: '打开桌宠设置', click: requestDesktopPetSettings },
      { type: 'separator' },
      ...(process.platform === 'darwin' ? [{
        label: '点击穿透',
        type: 'checkbox',
        checked: clickThrough,
        click: (menuItem) => {
          clickThrough = Boolean(menuItem.checked);
          mainWindow?.setIgnoreMouseEvents(clickThrough, { forward: true });
          updateTrayMenu();
        },
      }] : []),
      { label: '隐藏桌宠', click: emergencyHideWindow },
      { type: 'separator' },
      { label: '关闭桌宠', click: safeShutdown },
    ]),
  );
}

function handleMainWindowStartupFailure(error) {
  console.error('[CyberCore Desktop] startup failed:', error);
  reportPetStatus('error');
  emergencyHideWindow();
  safeShutdown({ source: 'local_surface_startup_failed' });
}

ipcMain.handle('v8-desktop:set-click-through', (_event, enabled) => {
  if (process.platform === 'win32') return false;
  clickThrough = Boolean(enabled);
  mainWindow?.setIgnoreMouseEvents(clickThrough, { forward: true });
  updateTrayMenu();
  return true;
});

ipcMain.handle('v8-desktop:set-companion-scale', (_event, scaleValue) => {
  return { width: 380, height: 380 };
});

ipcMain.handle('v8-desktop:set-always-on-top', (_event, enabled) => {
  mainWindow?.setAlwaysOnTop(Boolean(enabled), 'floating');
  return true;
});

ipcMain.handle('v8-desktop:set-panel-open', (_event, enabled) => {
  panelOpen = Boolean(enabled);
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setFocusable(panelOpen);
    if (panelOpen) {
      mainWindow.focus();
    }
  }
  updateTrayMenu();
  return null;
});

ipcMain.handle('v8-desktop:move-window-by', (_event, dx, dy) => {
  return false;
});

ipcMain.handle('v8-desktop:open-admin', () => {
  return requestDesktopPetSettings();
});

ipcMain.handle('v8-desktop:report-status', (_event, payload) => {
  const state = String(payload?.state || 'waiting_v8os');
  if (!new Set(['waiting_v8os', 'connected', 'stopping', 'error']).has(state)) return false;
  return reportPetStatus(state, payload?.activeSessionId);
});

ipcMain.handle('v8-desktop:open-session', (_event, sessionId) => {
  return requestOpenSession(sessionId);
});

ipcMain.handle('v8-desktop:get-active-session', () => {
  return { sessionId: lastShellActiveSessionId || null };
});

ipcMain.on('v8-desktop:get-transport', (event) => {
  if (
    !mainWindow
    || mainWindow.isDestroyed()
    || event.sender !== mainWindow.webContents
    || !isTrustedRendererUrl(event.sender.getURL(), DEVELOPMENT_TRANSPORT)
  ) {
    event.returnValue = null;
    return;
  }
  event.returnValue = rendererTransportView(DEVELOPMENT_TRANSPORT || verifiedLocalTransport);
});

ipcMain.handle('v8-desktop:shutdown-ready', (_event, requestId) => {
  if (!shuttingDown || String(requestId || '') !== shutdownRequestId) return false;
  shellControlClient?.send('shutdown-ready', { requestId: shutdownRequestId, reason: 'renderer_ready' });
  setTimeout(() => finalizeShutdown('renderer_ready'), 40);
  return true;
});

ipcMain.on('v8-desktop:set-interaction-regions', (event, regions) => {
  if (
    process.platform !== 'win32'
    || !mainWindow
    || mainWindow.isDestroyed()
    || event.sender !== mainWindow.webContents
  ) return;
  const normalized = normalizeInteractionRegions(regions, mainWindow.getBounds());
  if (normalized.length < 1) return;
  try {
    mainWindow.setShape(normalized);
    interactionRegionReady = true;
    maybeShowMainWindow();
  } catch (error) {
    console.error('[CyberCore Desktop] failed to apply the bounded Windows interaction region', error);
    reportPetStatus('error');
    emergencyHideWindow();
    safeShutdown({ source: 'interaction_region_apply_failed' });
  }
});

ipcMain.handle('v8-desktop:get-media-permission-status', async (_event, kind) => {
  const normalizedKind = kind === 'camera' ? 'camera' : 'microphone';
  return {
    platform: process.platform,
    kind: normalizedKind,
    status: getMediaAccessStatus(normalizedKind),
  };
});

ipcMain.handle('v8-desktop:request-media-access', async (_event, kind) => {
  const normalizedKind = kind === 'camera' ? 'camera' : 'microphone';
  const granted = await requestMediaAccess(normalizedKind);
  return {
    platform: process.platform,
    kind: normalizedKind,
    granted,
    status: getMediaAccessStatus(normalizedKind),
  };
});

ipcMain.handle('v8-desktop:open-media-privacy-settings', (_event, kind) => {
  return openMediaPrivacySettings(kind === 'camera' ? 'camera' : 'microphone');
});

ipcMain.handle('v8-desktop:read-local-config', (_event, key) => {
  const config = readLocalConfigFile();
  if (typeof key === 'string' && key) {
    return config[key] || null;
  }
  return config;
});

ipcMain.handle('v8-desktop:write-local-config', (_event, key, value) => {
  if (typeof key !== 'string' || !key) return false;
  const config = readLocalConfigFile();
  config[key] = value || {};
  writeLocalConfigFile(config);
  return true;
});

ipcMain.handle('v8-desktop:quit', () => {
  return safeShutdown();
});

const hasSingleInstanceLock = app.requestSingleInstanceLock();
app.on('v8os-gpu-recovery-requested', (relaunchArgs) => {
  if (shuttingDown || gpuRecoveryRelaunchArgs) return;
  gpuRecoveryRelaunchArgs = Array.isArray(relaunchArgs) ? relaunchArgs.map(String) : [];
  safeShutdown({ source: 'gpu_recovery' });
});
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', showOrFocusWindow);
  app.on('before-quit', (event) => {
    if (shuttingDown) return;
    event.preventDefault();
    safeShutdown({ source: 'application_quit' });
  });
  app.whenReady().then(() => {
    app.setAppUserModelId('V8OS.CyberCoreDesktop');
    if (!DEVELOPMENT_TRANSPORT) {
      installStableRendererProtocol(protocol, net.fetch, () => verifiedLocalTransport);
    }
    setupDevelopmentContentSecurityPolicy();
    setupPermissionHandlers();
    startShellControlClient();
    startCanonicalConfigWatcher();
    void createMainWindow().catch(handleMainWindowStartupFailure);
    if (!MANAGED_BY_SHELL) createTray();
    globalShortcut.register('Control+Alt+V', showOrFocusWindow);

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0 && !shuttingDown) {
        void createMainWindow().catch(handleMainWindowStartupFailure);
      }
    });
  });
}

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  canonicalConfigWatcher?.stop();
  canonicalConfigWatcher = null;
  shellLifecycleWatchdog?.stop();
  shellLifecycleWatchdog = null;
  shellControlClient?.stop();
  shellControlClient = null;
  removeOwnedDesktopPetProcessDescriptor();
  killBundledServerTree();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    safeShutdown();
  }
});
