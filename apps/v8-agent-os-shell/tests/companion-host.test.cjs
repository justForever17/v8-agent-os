const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { createRequire } = require('node:module');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-companion-contract-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.resolve(__dirname, '../../v8-agent-os-desktop-pet/electron/companion-window.cjs');
  const requireFile = createRequire(file);
  const handles = new Map(), events = new Map(), windows = [], statuses = [];
  const permissions = {};
  let appExit = 0;
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.id = windows.length + 1; this.destroyed = false;
      this.webContents = new EventEmitter();
      Object.assign(this.webContents, { mainFrame: { url: '' }, getURL: () => this.url,
        isLoading: () => false, setWindowOpenHandler() {}, send: (channel, data) => {
          if (channel === 'v8-desktop:prepare-shutdown' && !this.noAck) {
            setImmediate(() => handles.get('v8-desktop:shutdown-ready')(this.event(), data.requestId));
          }
        } }); windows.push(this);
    }
    event() { return { sender: this.webContents, senderFrame: this.webContents.mainFrame }; }
    isDestroyed() { return this.destroyed; }
    async loadURL(url) { this.url = url; this.webContents.mainFrame.url = url; this.emit('ready-to-show'); }
    getBounds() { return { x: 0, y: 0, width: 1920, height: 1080 }; }
    setShape() {} setIgnoreMouseEvents() {} setAlwaysOnTop() {} setSkipTaskbar() {}
    setFocusable() {} focus() {} show() {} hide() {} isMinimized() { return false; }
    destroy() { this.destroyed = true; this.emit('closed'); }
  }
  const partition = {
    protocol: { handle() {}, unhandle() {} }, webRequest: { onHeadersReceived() {} },
    setPermissionRequestHandler(fn) { permissions.request = fn; },
    setPermissionCheckHandler(fn) { permissions.check = fn; }, setDisplayMediaRequestHandler() {},
  };
  const electron = {
    app: { isPackaged: false, getPath: () => root, exit() { appExit++; }, quit() { appExit++; } },
    BrowserWindow: Window,
    globalShortcut: { register() {}, unregister() {}, unregisterAll() { throw Error('global shortcut owner violation'); } },
    ipcMain: { handle: (key, fn) => { assert.equal(handles.has(key), false); handles.set(key, fn); },
      on: (key, fn) => events.set(key, fn), removeHandler: key => handles.delete(key), removeListener: key => events.delete(key) },
    nativeImage: { createFromDataURL: () => ({ resize() { return this; } }), createFromPath: () => ({ isEmpty: () => false }) },
    session: { fromPartition: name => { assert.equal(name, 'persist:v8os-companion'); return partition; },
      get defaultSession() { throw Error('companion changed Product Web session'); } },
    screen: { getPrimaryDisplay: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1080 } }) },
    shell: {}, net: {}, protocol: {}, systemPreferences: {}, desktopCapturer: {},
  };
  const context = vm.createContext({ module: { exports: {} }, __dirname: path.dirname(file), console,
    process: { ...process, env: { V8_AGENT_OS_HOME: root, V8_DESKTOP_DEV_SERVER: 'http://127.0.0.1:22199' } },
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 80)), clearTimeout,
    require: name => name === 'electron' ? electron
      : name.endsWith('canonical-config-watcher.cjs') ? { createCanonicalConfigWatcher: () => ({ start() {}, stop() {} }) }
      : requireFile(name),
  });
  vm.runInContext(fs.readFileSync(file, 'utf8'), context);
  const host = context.module.exports.createCompanionWindow({ onStatus: value => statuses.push(value) });
  t.after(() => host.dispose());
  return { host, handles, events, windows, statuses, permissions, appExit: () => appExit, root };
}

test('companion start is idempotent, close acknowledges only its renderer, and restart leaves the Shell alive', async t => {
  const f = fixture(t);
  await Promise.all([f.host.start(), f.host.start()]);
  assert.equal(f.windows.length, 1);
  const window = f.windows[0];
  assert.equal(window.options.webPreferences.sandbox, true);
  assert.throws(() => f.handles.get('v8-desktop:quit')({ sender: {}, senderFrame: {} }), /untrusted_companion_sender/);
  assert.throws(() => f.handles.get('v8-desktop:quit')({ ...window.event(), senderFrame: { url: window.url } }), /untrusted_companion_sender/);
  assert.equal(f.permissions.check(window.webContents, 'microphone', 'https://untrusted.invalid'), false);
  const result = await f.host.stop();
  assert.equal(result.acked, true);
  assert.equal(result.stopped, true);
  assert.equal(f.host.status().running, false);
  assert.equal(f.appExit(), 0);
  await f.host.start();
  assert.equal(f.windows.length, 2);
  assert.equal(f.host.status().running, true);
  assert.equal(fs.existsSync(path.join(f.root, 'runtime', 'desktop-pet.json')), false);
});

test('an unresponsive or crashed skin closes within the bounded deadline without exiting the host', async t => {
  const f = fixture(t);
  await f.host.start();
  f.windows[0].noAck = true;
  f.windows[0].webContents.emit('render-process-gone');
  const result = await f.host.stop();
  assert.equal(result.acked, false);
  assert.equal(result.reason, 'renderer_timeout');
  assert.equal(f.host.status().running, false);
  assert.equal(f.appExit(), 0);
  await f.host.dispose();
  assert.equal(f.handles.size, 0);
  assert.equal(f.events.size, 0);
});
