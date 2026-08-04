const fs = require('node:fs');
const path = require('node:path');
const { app, BrowserWindow, ipcMain, net, protocol } = require('electron');
const {
  STABLE_RENDERER_ENTRY_URL,
  createVerifiedTransport,
  installStableRendererProtocol,
  isStableRendererUrl,
  registerStableRendererScheme,
  rendererTransportView,
} = require('../lib/stable-renderer-transport.cjs');

registerStableRendererScheme(protocol);

const userDataPath = String(process.env.V8_TEST_USER_DATA || '').trim();
const resultPath = String(process.env.V8_TEST_RESULT_PATH || '').trim();
const localBaseUrl = String(process.env.V8_TEST_BACKEND_BASE || '').trim();
const instanceId = String(process.env.V8_TEST_INSTANCE_ID || '').trim();
const serverPid = Number(process.env.V8_TEST_SERVER_PID);

if (!userDataPath || !resultPath || !localBaseUrl || !instanceId || !serverPid) {
  throw new Error('Electron stable-origin harness configuration is incomplete');
}

app.setPath('userData', userDataPath);
app.commandLine.appendSwitch('disable-gpu');

async function verifyFixtureIdentity() {
  const response = await net.fetch(`${localBaseUrl}/api/pet/health`, { cache: 'no-store' });
  const payload = await response.json();
  const expectedPort = Number(new URL(localBaseUrl).port);
  if (
    !response.ok
    || payload?.ok !== true
    || payload?.instanceId !== instanceId
    || payload?.pid !== serverPid
    || payload?.port !== expectedPort
  ) {
    throw new Error('Electron stable-origin fixture identity failed');
  }
}

async function run() {
  await app.whenReady();
  await verifyFixtureIdentity();
  const transport = createVerifiedTransport({ baseUrl: localBaseUrl, instanceId, serverPid });
  installStableRendererProtocol(protocol, net.fetch, () => transport);

  let window = null;
  ipcMain.on('v8-desktop:get-transport', (event) => {
    if (!window || event.sender !== window.webContents || !isStableRendererUrl(event.sender.getURL())) {
      event.returnValue = null;
      return;
    }
    event.returnValue = rendererTransportView(transport);
  });

  window = new BrowserWindow({
    show: false,
    skipTaskbar: true,
    webPreferences: {
      preload: path.join(__dirname, '..', 'electron', 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  await window.loadURL(STABLE_RENDERER_ENTRY_URL);
  const result = await window.webContents.executeJavaScript(`
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 8000;
      const inspect = () => {
        if (window.__V8_TEST_RESULT__) {
          resolve(window.__V8_TEST_RESULT__);
          return;
        }
        if (window.__V8_TEST_ERROR__) {
          reject(new Error(window.__V8_TEST_ERROR__));
          return;
        }
        if (Date.now() >= deadline) {
          reject(new Error('renderer contract timed out'));
          return;
        }
        setTimeout(inspect, 20);
      };
      inspect();
    })
  `, true);
  fs.writeFileSync(resultPath, JSON.stringify(result), 'utf8');
  await new Promise((resolve) => setTimeout(resolve, 150));
  window.destroy();
  app.quit();
}

run().catch((error) => {
  fs.writeFileSync(resultPath, JSON.stringify({ error: error?.stack || String(error) }), 'utf8');
  app.exit(1);
});
