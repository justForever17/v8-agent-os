const { app, BrowserWindow, Menu, Tray, globalShortcut, ipcMain, nativeImage, shell, session, screen, systemPreferences, desktopCapturer } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { createShellControlClient } = require('../lib/shell-control-client.cjs');

let mainWindow = null;
let tray = null;
let clickThrough = false;
let bundledServer = null;
let panelOpen = false;
let companionClosedSize = { width: 380, height: 380 };
let shuttingDown = false;
let preExpansionBounds = null;
let shellControlClient = null;
let lastShellActiveSessionId = '';
let lastPetStatus = { state: 'waiting_v8os', activeSessionId: null };
let shutdownTimer = null;
let shutdownRequestId = '';

const LOCAL_SERVER_URL = 'http://127.0.0.1:3000';
const V8_WEB_URL = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
const MANAGED_BY_SHELL = process.env.V8_DESKTOP_PET_MANAGED_BY_SHELL === '1';
const SHELL_SETTINGS_DEEP_LINK = 'v8os://open/admin/desktop-pet';
const CLOSED_WIDTH = 380;
const CLOSED_HEIGHT = 380;
const PANEL_WIDTH = 940;
const PANEL_HEIGHT = 720;

function desktopPetProcessPath() {
  const stateRoot = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), '.v8-agent-os');
  return path.join(stateRoot, 'runtime', 'desktop-pet.json');
}

function writeDesktopPetProcessDescriptor() {
  const filePath = desktopPetProcessPath();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, JSON.stringify({
    version: 1,
    pid: process.pid,
    managedByShell: MANAGED_BY_SHELL,
    startedAt: new Date().toISOString(),
  }, null, 2), 'utf8');
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
    if (current?.pid === process.pid) fs.rmSync(filePath, { force: true });
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

function startBundledServer() {
  if (bundledServer || process.env.V8_DESKTOP_DEV_SERVER) {
    return;
  }
  const serverPath = path.join(__dirname, '..', 'dist', 'server.cjs');
  bundledServer = spawn(process.env.V8_DESKTOP_NODE || 'node', [serverPath], {
    cwd: path.join(__dirname, '..'),
    env: {
      ...process.env,
      NODE_ENV: 'production',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  bundledServer.stdout.on('data', (chunk) => {
    console.log(`[CyberCore Server] ${String(chunk).trim()}`);
  });
  bundledServer.stderr.on('data', (chunk) => {
    console.warn(`[CyberCore Server] ${String(chunk).trim()}`);
  });
  bundledServer.on('exit', (code, signal) => {
    console.warn('[CyberCore Server] exited', { code, signal });
    bundledServer = null;
  });
}

function killBundledServerTree() {
  const child = bundledServer;
  if (!child) return;
  bundledServer = null;
  const pid = child.pid;
  try {
    child.removeAllListeners('exit');
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
    mainWindow.setIgnoreMouseEvents(false);
  } catch {}
  try {
    mainWindow.setAlwaysOnTop(false);
  } catch {}
  try {
    mainWindow.setSkipTaskbar(true);
  } catch {}
}

function emergencyHideWindow() {
  clickThrough = false;
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
  shellControlClient = createShellControlClient({
    onConnected() {
      reportPetStatus(lastPetStatus.state, lastPetStatus.activeSessionId);
      sendToRenderer('v8-desktop:shell-active-session', { sessionId: lastShellActiveSessionId || null });
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
  app.exit(0);
  return true;
}

function safeShutdown(options = {}) {
  if (shuttingDown) return true;
  shuttingDown = true;
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
  const allowMediaForLocalApp = (webContents) => {
    const url = webContents?.getURL?.() || '';
    // If the URL is empty or about:blank (which happens during initialization), allow it
    if (!url || url === 'about:blank') {
      return true;
    }
    // Allow any localhost, 127.0.0.1 on any port, or file: origin
    return (
      url.startsWith('http://localhost:') ||
      url.startsWith('http://127.0.0.1:') ||
      url.startsWith('file:')
    );
  };

  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const isAllowedPermission = 
      permission === 'media' || 
      permission === 'camera' || 
      permission === 'microphone' || 
      permission === 'speaker-selection' ||
      permission === 'audioCapture' ||
      permission === 'videoCapture';
      
    if (isAllowedPermission && allowMediaForLocalApp(webContents)) {
      const requested = details?.mediaTypes || [];
      callback(!requested.length || requested.includes('video') || requested.includes('audio'));
      return;
    }
    callback(false);
  });

  session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
    const isAllowedPermission = 
      permission === 'media' || 
      permission === 'camera' || 
      permission === 'microphone' || 
      permission === 'speaker-selection' ||
      permission === 'audioCapture' ||
      permission === 'videoCapture';

    if (isAllowedPermission) {
      return allowMediaForLocalApp(webContents);
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

async function waitForLocalServer(timeoutMs = 10000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(LOCAL_SERVER_URL, { method: 'GET' });
      if (response.ok) {
        return true;
      }
    } catch {
      // Server is still warming up.
    }
    await new Promise((resolve) => setTimeout(resolve, 240));
  }
  return false;
}

async function resolveEntry() {
  if (process.env.V8_DESKTOP_DEV_SERVER) {
    return { type: 'url', value: process.env.V8_DESKTOP_DEV_SERVER };
  }
  startBundledServer();
  await waitForLocalServer();
  return { type: 'url', value: LOCAL_SERVER_URL };
}

async function createMainWindow() {
  const trayIcon = createTrayIcon();
  const primaryDisplay = screen.getPrimaryDisplay();
  const { x, y, width, height } = primaryDisplay.workArea;

  mainWindow = new BrowserWindow({
    x,
    y,
    width,
    height,
    frame: false,
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

  const entry = await resolveEntry();
  void mainWindow.loadURL(entry.value);

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('[CyberCore Desktop] load failed:', errorCode, errorDescription, validatedURL);
    mainWindow?.setBackgroundColor('#0f172a');
    mainWindow?.show();
  });

  mainWindow.webContents.on('console-message', (_event, level, message) => {
    const prefix = level >= 2 ? 'warn' : 'log';
    console[prefix]('[CyberCore Desktop renderer]', message);
  });

  mainWindow.webContents.on('did-finish-load', () => {
    mainWindow?.show();
    sendToRenderer('v8-desktop:shell-active-session', { sessionId: lastShellActiveSessionId || null });
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
    mainWindow?.setIgnoreMouseEvents(true, { forward: true });
    clickThrough = true;
    updateTrayMenu();
  });

  mainWindow.on('close', (event) => {
    if (shuttingDown) return;
    event.preventDefault();
    safeShutdown({ source: 'window' });
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function resizeForPanel(open) {
  panelOpen = Boolean(open);
  return null;
}

function showOrFocusWindow() {
  if (!mainWindow) {
    createMainWindow();
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
      {
        label: '点击穿透',
        type: 'checkbox',
        checked: clickThrough,
        click: (menuItem) => {
          clickThrough = Boolean(menuItem.checked);
          mainWindow?.setIgnoreMouseEvents(clickThrough, { forward: true });
          updateTrayMenu();
        },
      },
      { label: '隐藏桌宠', click: emergencyHideWindow },
      { type: 'separator' },
      { label: '关闭桌宠', click: safeShutdown },
    ]),
  );
}

ipcMain.handle('v8-desktop:set-click-through', (_event, enabled) => {
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

ipcMain.handle('v8-desktop:shutdown-ready', (_event, requestId) => {
  if (!shuttingDown || String(requestId || '') !== shutdownRequestId) return false;
  shellControlClient?.send('shutdown-ready', { requestId: shutdownRequestId, reason: 'renderer_ready' });
  setTimeout(() => finalizeShutdown('renderer_ready'), 40);
  return true;
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
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', showOrFocusWindow);
  app.whenReady().then(() => {
    app.setAppUserModelId('V8OS.CyberCoreDesktop');
    writeDesktopPetProcessDescriptor();
    setupPermissionHandlers();
    startShellControlClient();
    void createMainWindow();
    if (!MANAGED_BY_SHELL) createTray();
    globalShortcut.register('Control+Alt+V', showOrFocusWindow);

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });
}

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
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
