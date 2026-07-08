const { app, BrowserWindow, Menu, Tray, globalShortcut, ipcMain, nativeImage, shell, session, screen, systemPreferences, desktopCapturer } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let mainWindow = null;
let tray = null;
let clickThrough = false;
let bundledServer = null;
let panelOpen = false;
let companionClosedSize = { width: 380, height: 380 };
let shuttingDown = false;
let preExpansionBounds = null;

const LOCAL_SERVER_URL = 'http://127.0.0.1:3000';
const V8_ADMIN_URL = process.env.V8_ADMIN_BASE_URL || 'http://127.0.0.1:9528';
const V8_WEB_URL = process.env.V8_WEB_BASE_URL || 'http://127.0.0.1:9527';
const MANAGED_BY_SHELL = process.env.V8_DESKTOP_PET_MANAGED_BY_SHELL === '1';
const CLOSED_WIDTH = 380;
const CLOSED_HEIGHT = 380;
const PANEL_WIDTH = 940;
const PANEL_HEIGHT = 720;

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

function sendPrepareShutdown() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.webContents.send('v8-desktop:prepare-shutdown');
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

function safeShutdown() {
  if (shuttingDown) return true;
  shuttingDown = true;
  sendPrepareShutdown();
  neutralizeDesktopOverlay();
  try {
    globalShortcut.unregisterAll();
  } catch {}
  killBundledServerTree();
  setTimeout(() => {
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
    app.exit(0);
  }, 600);
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
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
    mainWindow?.setIgnoreMouseEvents(true, { forward: true });
    clickThrough = true;
    updateTrayMenu();
  });

  mainWindow.on('close', () => {
    sendPrepareShutdown();
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
      { label: '打开桌宠设置', click: () => openLocalProduct(V8_ADMIN_URL, '/admin/desktop-pet') },
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

ipcMain.handle('v8-desktop:open-admin', async (_event, url) => {
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return false;
  await shell.openExternal(url);
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

app.whenReady().then(() => {
  app.setAppUserModelId('V8OS.CyberCoreDesktop');
  setupPermissionHandlers();
  void createMainWindow();
  if (!MANAGED_BY_SHELL) createTray();
  globalShortcut.register('Control+Alt+V', showOrFocusWindow);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  killBundledServerTree();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    safeShutdown();
  }
});
