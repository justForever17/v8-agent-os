const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { pathToFileURL } = require('node:url');
const { createDesktopFaultRecorder, desktopFaultLogPath } = require('../lib/desktop-fault-log.cjs');

const shellRoot = path.resolve(__dirname, '..');
function fixture(t, maxBytes) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-shell-fault-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const logPath = desktopFaultLogPath({ V8_AGENT_OS_HOME: root });
  return {
    root, logPath,
    record: createDesktopFaultRecorder({ logPath, maxBytes }),
    rows: () => fs.readFileSync(logPath, 'utf8').trim().split('\n').map(JSON.parse),
  };
}

test('direct desktop launch uses the existing CLI log directory without requiring the CLI process', async (t) => {
  const { LOG_DIR } = await import(pathToFileURL(path.resolve(shellRoot, '../v8-agent-os-cli/src/paths.mjs')));
  assert.equal(path.dirname(desktopFaultLogPath()), LOG_DIR);
  const f = fixture(t);
  assert.equal(f.record('renderer-process-exited', { reason: 'oom', exitCode: -1, surface: 'web' }), true);
  assert.equal(f.rows()[0].reason, 'oom');
  assert.equal(path.basename(desktopFaultLogPath({ V8_AGENT_OS_HOME: f.root, V8OS_DESKTOP_RUNTIME_MODE: 'desktop-pet' })), 'desktop-pet-faults.jsonl');
});

test('rotation bounds both files and preserves complete recent records', (t) => {
  const f = fixture(t, 1024);
  for (let i = 0; i < 30; i++) assert.equal(f.record('renderer-process-exited', { exitCode: i, reason: 'crashed' }), true);
  for (const filename of [f.logPath, `${f.logPath}.1`]) {
    assert.ok(fs.statSync(filename).size <= 1024);
    for (const line of fs.readFileSync(filename, 'utf8').trim().split('\n')) assert.doesNotThrow(() => JSON.parse(line));
  }
  assert.equal(f.rows().at(-1).exitCode, 29);
  assert.equal(fs.readdirSync(path.dirname(f.logPath)).length, 2);
  fs.writeFileSync(f.logPath, 'x'.repeat(4096));
  assert.equal(f.record('surface-recovery', { stage: 'scheduled' }), true);
  assert.ok(fs.statSync(f.logPath).size <= 1024);
  assert.equal(fs.existsSync(`${f.logPath}.1`), false);
});

test('filesystem failures do not escape or break subsequent recovery', (t) => {
  const f = fixture(t);
  fs.mkdirSync(f.logPath, { recursive: true });
  assert.equal(f.record('renderer-process-exited', { reason: 'oom' }), false);
  fs.rmdirSync(f.logPath);
  assert.equal(f.record('surface-recovery', { stage: 'recovered' }), true);
  assert.equal(f.rows()[0].stage, 'recovered');
});

test('only enum fields and numeric exit codes survive, including hostile values in approved keys', (t) => {
  const f = fixture(t);
  const secret = 'DO-NOT-LOG-private-fixture';
  f.record('gpu-process-exited', {
    reason: secret, stage: secret, surface: secret, exitCode: secret,
    url: `https://fixture.invalid/?token=${secret}`, serviceName: secret,
    argv: [secret], password: secret, prompt: secret,
  });
  assert.equal(f.record(secret, {}), false);
  const [record] = f.rows();
  assert.deepEqual(Object.keys(record).sort(), ['event', 'exitCode', 'pid', 'reason', 'stage', 'surface', 'timestamp']);
  assert.equal(record.reason, 'unknown');
  assert.equal(record.exitCode, null);
  assert.ok(!fs.readFileSync(f.logPath, 'utf8').includes(secret));
});

test('the real bootstrap GPU event records exits and one governed recovery request', (t) => {
  const f = fixture(t);
  const app = new EventEmitter();
  Object.assign(app, { isPackaged: false, commandLine: { hasSwitch: () => false, appendSwitch() {} } });
  const environment = {};
  vm.runInNewContext(fs.readFileSync(path.join(shellRoot, 'electron/bootstrap.cjs'), 'utf8'), {
    __dirname: path.join(shellRoot, 'electron'),
    process: { env: environment, argv: ['fixture'], platform: 'win32', execPath: 'fixture.exe' },
    console: { warn() {}, error() {} },
    require(name) {
      if (name === 'electron') return { app };
      if (name === '../lib/desktop-fault-log.cjs') return { recordDesktopFault: f.record };
      if (name === '../lib/gpu-recovery.cjs') return require('../lib/gpu-recovery.cjs');
      if (name === './main.cjs') return {};
      return require(name);
    },
  });
  app.emit('child-process-gone', {}, { type: 'Utility', reason: 'crashed' });
  assert.equal(fs.existsSync(f.logPath), false);
  let requested = 0;
  app.on('v8os-gpu-recovery-requested', () => { requested++; });
  app.emit('child-process-gone', {}, { type: 'GPU', reason: 'crashed', exitCode: 41, serviceName: 'private fixture' });
  app.emit('child-process-gone', {}, { type: 'GPU', reason: 'oom', exitCode: 42 });
  assert.deepEqual(f.rows().map((row) => [row.event, row.stage, row.exitCode]), [
    ['gpu-process-exited', 'observed', 41], ['gpu-process-exited', 'observed', 42], ['surface-recovery', 'relaunch-requested', null],
  ]);
  assert.equal(requested, 1);
});

test('the actual renderer event records sanitized surface and forwards recovery', (t) => {
  const f = fixture(t);
  const source = fs.readFileSync(path.join(shellRoot, 'electron/main.cjs'), 'utf8');
  const start = source.indexOf("mainWindow.webContents.on('render-process-gone'");
  const end = source.indexOf("mainWindow.webContents.on('did-fail-load'", start);
  assert.ok(start > 0 && end > start);
  const webContents = new EventEmitter();
  webContents.getURL = () => 'http://127.0.0.1:9527/chat?id=PRIVATE-FIXTURE';
  const recoveries = [];
  vm.runInNewContext(source.slice(start, end), {
    mainWindow: { webContents }, shellControl: { setSurfaceStatus() {} },
    coreServicesReady: true, webBaseUrl: 'http://127.0.0.1:9527', adminBaseUrl: 'http://127.0.0.1:9528',
    classifyProductSurface: require('../lib/readiness-probe.cjs').classifyProductSurface,
    recordDesktopFault: f.record, scheduleSurfaceRecovery: (...args) => recoveries.push(args),
  });
  webContents.emit('render-process-gone', {}, { reason: 'oom', exitCode: -2, prompt: 'PRIVATE-FIXTURE' });
  const [row] = f.rows();
  assert.equal(row.reason, 'oom');
  assert.equal(row.surface, 'web');
  assert.equal(row.exitCode, -2);
  assert.ok(!fs.readFileSync(f.logPath, 'utf8').includes('PRIVATE-FIXTURE'));
  assert.equal(recoveries.length, 1);
  webContents.emit('render-process-gone', {}, { reason: 'clean-exit', exitCode: 0 });
  assert.equal(f.rows().length, 1);
});
