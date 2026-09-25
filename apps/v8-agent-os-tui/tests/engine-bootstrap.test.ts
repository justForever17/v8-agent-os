import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import http from 'node:http';
import { create, Header } from 'tar';
import { installEngine, installedRuntime, releaseVersion, extractEngineArchive, validateManifest, targetForPlatform, rememberedDesktopRuntime, runtimeProfileForManifest, validateRuntimeIdentity, acquireDesktopLease, promoteStagedRuntime, discoverHostPython, discoverHostBrowser } from '../bin/engine-bootstrap.mjs';

const VERSION = '2026.09.22.1', TARGET = 'linux-x64', COMMIT = 'a'.repeat(40);
const ROOT = `v8os-engine-${VERSION}-${TARGET}`;
const manifest = () => ({ schema: 1, profile: 'engine', version: VERSION, target: TARGET, sourceCommit: COMMIT, sourceDirty: false,
  engineDir: 'apps/v8-agent-os-engine', python: 'apps/v8-agent-os-engine/.python/bin/python3', cli: 'apps/v8-agent-os-cli/bin/v8os.mjs' });
const put = (filename: string, content: string, mode = 0o600) => { fs.mkdirSync(path.dirname(filename), { recursive: true }); fs.writeFileSync(filename, content, { mode }); };
const hash = (filename: string) => createHash('sha256').update(fs.readFileSync(filename)).digest('hex');

async function fixture(t: any, mutate = (_manifest: any) => {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-engine-installer-'));
  const keys = ['V8_AGENT_OS_HOME', 'V8OS_ENGINE_RUNTIME_ROOT', 'V8OS_ENGINE_RUNTIME_DIR', 'V8OS_ENGINE_MANIFEST_URL', 'V8OS_ENGINE_ARCHIVE', 'V8OS_RELEASE_SOURCE_COMMIT'];
  const previous = new Map(keys.map(key => [key, process.env[key]]));
  for (const key of keys) delete process.env[key];
  t.after(() => { for (const [key, value] of previous) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } fs.rmSync(dir, { recursive: true, force: true }); });
  const inside = manifest(); mutate(inside);
  put(path.join(dir, ROOT, 'engine-manifest.json'), JSON.stringify(inside));
  put(path.join(dir, ROOT, 'apps/v8-agent-os-engine/main.py'), '# fixture');
  put(path.join(dir, ROOT, manifest().python), '#!/bin/sh\nexit 0\n', 0o755);
  put(path.join(dir, ROOT, manifest().cli), '// fixture');
  const archive = path.join(dir, `V8OS-Engine-${VERSION}-${TARGET}.tar.gz`);
  await create({ cwd: dir, file: archive, gzip: true }, [ROOT]);
  const publicManifest = path.join(dir, 'download.json');
  const remote = { schema: 1, profile: 'engine', version: VERSION, target: TARGET, sourceCommit: COMMIT,
    root: ROOT, asset: path.basename(archive), sha256: hash(archive) };
  put(publicManifest, JSON.stringify(remote));
  process.env.V8_AGENT_OS_HOME = path.join(dir, 'state');
  process.env.V8OS_ENGINE_MANIFEST_URL = pathToFileURL(publicManifest).href;
  process.env.V8OS_ENGINE_ARCHIVE = archive;
  return { dir, archive, publicManifest, remote, destination: path.join(dir, 'state/runtime/engine', VERSION, TARGET) };
}

test('release identity preserves zero-padded Release dates and rejects invalid targets', () => {
  assert.equal(releaseVersion('2026.9.2-1'), '2026.09.02.1');
  assert.equal(releaseVersion('2026.09.22.1'), VERSION);
  assert.throws(() => releaseVersion('2026.2.30-1'), /date/);
  assert.throws(() => releaseVersion('../../latest'), /version/);
  assert.equal(targetForPlatform('win32', 'x64'), 'windows-x64');
  assert.equal(targetForPlatform('win32', 'arm64'), 'windows-arm64');
  assert.equal(targetForPlatform('darwin', 'x64'), 'macos-x64');
  assert.equal(targetForPlatform('darwin', 'arm64'), 'macos-arm64');
  assert.throws(() => targetForPlatform('linux', 'arm64'), /unavailable/);
  assert.throws(() => targetForPlatform('freebsd', 'x64'), /unavailable/);
  assert.throws(() => targetForPlatform('linux', 'x64', '2.17'), /glibc 2.35/);
  assert.equal(targetForPlatform('linux', 'x64', '2.35'), TARGET);
  assert.throws(() => validateManifest({ ...manifest(), python: '../python' }), /entrypoints/);
});

test('desktop Engine manifests use the platform Python entrypoint', () => {
  assert.doesNotThrow(() => validateManifest({ ...manifest(), target: 'windows-x64', python: 'apps/v8-agent-os-engine/.python/python.exe' }, { version: VERSION, target: 'windows-x64', sourceCommit: COMMIT }));
  assert.doesNotThrow(() => validateManifest({ ...manifest(), target: 'macos-arm64', python: 'apps/v8-agent-os-engine/.python/bin/python3' }, { version: VERSION, target: 'macos-arm64', sourceCommit: COMMIT }));
  assert.throws(() => validateManifest({ ...manifest(), target: 'windows-x64' }, { version: VERSION, target: 'windows-x64', sourceCommit: COMMIT }), /entrypoints/);
});

test('legacy Linux server Engine manifests retain the server runtime profile', () => {
  assert.equal(runtimeProfileForManifest({ target: 'linux-x64' }), 'server');
  assert.equal(runtimeProfileForManifest({ target: 'windows-x64' }), 'desktop');
  assert.equal(runtimeProfileForManifest({ target: 'linux-x64', runtimeProfile: 'desktop' }), 'desktop');
});

test('discovered daemon must match the npm release identity before reuse', async t => {
  const f = await fixture(t);
  await installEngine({ version: VERSION, target: TARGET });
  assert.doesNotThrow(() => validateRuntimeIdentity(f.destination, { version: VERSION, sourceCommit: COMMIT }));
  const manifestPath = path.join(f.destination, 'engine-manifest.json');
  const original = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  fs.writeFileSync(manifestPath, JSON.stringify({ ...original, sourceCommit: 'b'.repeat(40) }));
  assert.throws(() => validateRuntimeIdentity(f.destination, { version: VERSION, sourceCommit: COMMIT }), /identity mismatch/);
  fs.writeFileSync(manifestPath, JSON.stringify({ ...original, version: '2026.09.22.2' }));
  assert.throws(() => validateRuntimeIdentity(f.destination, { version: VERSION, sourceCommit: COMMIT }), /identity mismatch/);
  fs.writeFileSync(manifestPath, JSON.stringify({ ...original, sourceDirty: true }));
  assert.throws(() => validateRuntimeIdentity(f.destination, { version: VERSION, sourceCommit: COMMIT }), /identity mismatch/);
});

test('desktop Engine is released only after the last live TUI lease', async t => {
  const f = await fixture(t);
  let stops = 0;
  const stop = async () => { stops += 1; return { status: 'stopped' }; };
  const first = await acquireDesktopLease({ identity: { pid: process.pid, launchId: 'engine' }, stop });
  const second = await acquireDesktopLease({ identity: { pid: process.pid, launchId: 'engine' }, stop });
  assert.equal((await first.release()).status, 'lease_retained');
  assert.equal(stops, 0);
  assert.equal((await second.release()).status, 'stopped');
  assert.equal(stops, 1);
  await t.test('stale lease is reclaimed', async () => {
    const root = path.join(process.env.V8_AGENT_OS_HOME!, 'runtime/cli/tui-leases');
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, 'stale.json'), JSON.stringify({ ownerPid: 99999999 }));
    const lease = await acquireDesktopLease({ identity: { pid: process.pid, launchId: 'engine' }, stop });
    assert.equal(fs.existsSync(path.join(root, 'stale.json')), false);
    await lease.release();
  });
  await t.test('a replaced Engine identity cannot be pinned by an older TUI lease', async () => {
    let oldStops = 0, newStops = 0;
    const old = await acquireDesktopLease({ identity: { pid: process.pid, launchId: 'engine-old' }, stop: async () => { oldStops++; return { status: 'not_owned' }; } });
    const current = await acquireDesktopLease({ identity: { pid: process.pid, launchId: 'engine-new' }, stop: async () => { newStops++; return { status: 'stopped' }; } });
    assert.equal((await current.release()).status, 'stopped');
    assert.equal(newStops, 1);
    assert.equal(oldStops, 0);
    await old.release();
    assert.equal(oldStops, 1);
  });
});

test('an older managed service is blocked before lifecycle control is contacted', async t => {
  const f = await fixture(t);
  const packageDir = path.join(f.dir, 'npm-service-identity');
  put(path.join(packageDir, 'bin/engine-bootstrap.mjs'), fs.readFileSync(path.resolve('bin/engine-bootstrap.mjs'), 'utf8'));
  put(path.join(packageDir, 'package.json'), JSON.stringify({ type: 'module', version: '2026.09.24-3', v8Release: { version: VERSION, sourceCommit: COMMIT } }));
  fs.symlinkSync(path.resolve('node_modules'), path.join(packageDir, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  put(path.join(packageDir, 'dist/core-control.mjs'), `
    export const discoverServerServiceReceipt = () => ({ bundleRoot: ${JSON.stringify(f.destination)}, version: '2026.09.21.1', port: 9530 });
    export async function startCoreComponentsWithRuntimePorts() { throw new Error('lifecycle control must not be contacted'); }
  `);
  const script = `import { startEngine } from ${JSON.stringify(pathToFileURL(path.join(packageDir, 'bin/engine-bootstrap.mjs')).href)};
    try { await startEngine({ install: false }); process.exitCode = 2; }
    catch (error) { console.log(error.message); }`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8', env: process.env });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /service receipt is 2026\.09\.21\.1/);
  assert.doesNotMatch(result.stdout, /lifecycle control must not be contacted/);
});

test('actual tar extraction installs the complete immutable runtime once under parallel starts', async t => {
  const f = await fixture(t);
  const results = await Promise.all([installEngine({ version: VERSION, target: TARGET }), installEngine({ version: VERSION, target: TARGET })]);
  assert.equal(results.filter(r => r.installed).length, 1);
  assert.equal(installedRuntime({ version: VERSION, target: TARGET }), f.destination);
  assert.equal(fs.readFileSync(path.join(f.destination, manifest().python), 'utf8'), '#!/bin/sh\nexit 0\n');
  if (process.platform !== 'win32') fs.accessSync(path.join(f.destination, manifest().python), fs.constants.X_OK);
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)), [TARGET]);
  fs.writeFileSync(path.join(f.destination, 'kept'), 'immutable');
  assert.equal((await installEngine({ version: VERSION, target: TARGET })).installed, false);
  assert.equal(fs.readFileSync(path.join(f.destination, 'kept'), 'utf8'), 'immutable');
});

test('a stopped desktop remains discoverable while old portable versions cannot shadow npm upgrades', async t => {
  const f = await fixture(t);
  const desktop = path.join(f.dir, 'desktop');
  put(path.join(desktop, 'apps/v8-agent-os-engine/main.py'), '# desktop engine');
  put(path.join(desktop, 'apps/v8-agent-os-cli/bin/v8os.mjs'), '// cli');
  assert.equal(fs.existsSync(path.join(desktop, 'apps/v8-agent-os-web')), false);
  put(path.join(process.env.V8_AGENT_OS_HOME!, 'runtime/cli/processes.json'), JSON.stringify({ version: 1, repoRoot: desktop, processes: {} }));
  assert.equal(rememberedDesktopRuntime(), desktop);

  put(path.join(desktop, 'engine-manifest.json'), JSON.stringify(manifest()));
  assert.equal(rememberedDesktopRuntime(), '');
});

test('hash mismatch, cancel and wrong internal commit leave no installed or partial runtime', async t => {
  const f = await fixture(t, m => { m.sourceCommit = 'b'.repeat(40); });
  await assert.rejects(installEngine({ version: VERSION, target: TARGET }), /identity mismatch/);
  put(f.publicManifest, JSON.stringify({ ...f.remote, sha256: '0'.repeat(64) }));
  await assert.rejects(installEngine({ version: VERSION, target: TARGET }), /SHA-256/);
  await assert.rejects(installEngine({ version: VERSION, target: TARGET, signal: AbortSignal.abort(new Error('test cancelled')) }), /cancelled/);
  assert.equal(fs.existsSync(f.destination), false);
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)), []);
});

test('failed candidate installation preserves the previous installed version and user data', async t => {
  const f = await fixture(t);
  await installEngine({ version: VERSION, target: TARGET });
  put(path.join(process.env.V8_AGENT_OS_HOME!, 'user-data'), 'preserve me');
  await assert.rejects(installEngine({ version: '2026.09.22.2', target: TARGET }), /identity mismatch/);
  assert.equal(installedRuntime({ version: VERSION, target: TARGET }), f.destination);
  assert.equal(fs.readFileSync(path.join(process.env.V8_AGENT_OS_HOME!, 'user-data'), 'utf8'), 'preserve me');
});

test('streamed download succeeds without a local archive; interrupted transfer is retryable', async t => {
  const f = await fixture(t);
  delete process.env.V8OS_ENGINE_ARCHIVE;
  let interrupt = true;
  const contents = fs.readFileSync(f.archive);
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { 'content-type': 'application/gzip', 'content-length': contents.length });
    res.write(contents.subarray(0, 50));
    setImmediate(() => { if (interrupt) res.destroy(); else res.end(contents.subarray(50)); });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>(resolve => server.close(() => resolve())));
  const address = server.address() as { port: number };
  put(f.publicManifest, JSON.stringify({ ...f.remote, assetUrl: `http://127.0.0.1:${address.port}/asset` }));
  await assert.rejects(installEngine({ version: VERSION, target: TARGET }));
  assert.equal(fs.existsSync(f.destination), false);
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)), []);
  interrupt = false;
  assert.equal((await installEngine({ version: VERSION, target: TARGET })).installed, true);
});

test('archive traversal, links outside the runtime and duplicate paths are rejected before extraction', async t => {
  const f = await fixture(t);
  const invalidCases = [
    [{ path: ROOT + '/../escape', type: 'File' }],
    [{ path: ROOT + '/link', type: 'SymbolicLink', linkpath: '../../outside' }],
    [{ path: ROOT + '/hard', type: 'Link', linkpath: 'outside' }],
    [{ path: ROOT + '/same', type: 'File' }, { path: ROOT + '/same', type: 'File' }],
  ];
  for (let i = 0; i < invalidCases.length; i++) {
    const buffers = invalidCases[i].map(entry => {
      const block = Buffer.alloc(512); new Header({ ...entry, size: 0, mode: 0o644 }).encode(block); return block;
    });
    const archive = path.join(f.dir, `invalid-${i}.tar.gz`);
    fs.writeFileSync(archive, gzipSync(Buffer.concat([...buffers, Buffer.alloc(1024)])));
    const destination = path.join(f.dir, `extract-${i}`);
    await assert.rejects(extractEngineArchive(archive, destination, ROOT), /Unsafe|escapes|Duplicate/);
    assert.equal(fs.existsSync(destination), false);
  }
});

test('unified command rejects non-TTY before downloads and help does not import a renderer', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-unified-entry-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const entry = path.resolve('bin/v8os.mjs');
  const env = { ...process.env, V8_AGENT_OS_HOME: dir, V8OS_ENGINE_MANIFEST_URL: 'https://unreachable.invalid/manifest' };
  const run = (args: string[]) => spawnSync(process.execPath, [entry, ...args], { encoding: 'utf8', env });
  const result = run(['--json']);
  assert.equal(result.status, 2); assert.equal(JSON.parse(result.stdout).error, 'tty_required');
  assert.equal(run(['--help']).status, 0);
  assert.match(run(['--help', '--lang', 'en']).stdout, /Unified CLI/);
  assert.deepEqual(fs.readdirSync(dir), []);
});

test('explicit service upgrade installs the matching archive before handing off to the service owner', async t => {
  const f = await fixture(t);
  const packageDir = path.join(f.dir, 'npm');
  const boot = path.join(packageDir, 'bin/engine-bootstrap.mjs');
  put(boot, fs.readFileSync(path.resolve('bin/engine-bootstrap.mjs'), 'utf8'));
  put(path.join(packageDir, 'package.json'), JSON.stringify({ type: 'module', version: '2026.9.22-1', v8Release: { version: VERSION, sourceCommit: COMMIT } }));
  fs.symlinkSync(path.resolve('node_modules'), path.join(packageDir, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  // The real installer runs; only the external systemd owner is represented by
  // a recorder so this contract cannot alter the host's actual user service.
  put(path.join(packageDir, 'dist/core-control.mjs'), 'export const discoverServerServiceReceipt = () => null;');
  put(path.join(packageDir, 'dist/cli.mjs'), `import fs from 'node:fs'; import path from 'node:path';
    export async function main(args) {
      const root = args[args.indexOf('--bundle') + 1];
      const receipt = JSON.parse(fs.readFileSync(path.join(root, '.installed-receipt.json')));
      console.log(JSON.stringify({args, version:receipt.version, runtime: process.env.V8_ENGINE_PYTHON}));
    }`);
  const script = `import {runEngineCli} from ${JSON.stringify(pathToFileURL(boot).href)};
    await runEngineCli(['service','upgrade','--json'], {version:${JSON.stringify(VERSION)},target:${JSON.stringify(TARGET)}});`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8', env: process.env });
  assert.equal(result.status, 0, result.stderr);
  const receipt = JSON.parse(result.stdout);
  assert.deepEqual(receipt.args, ['service', 'upgrade', '--json', '--bundle', f.destination]);
  assert.equal(receipt.version, VERSION);
  assert.equal(receipt.runtime, path.join(f.destination, manifest().python));
});

test('promoteStagedRuntime atomically promotes staged Engine during installedRuntime discovery', async t => {
  const f = await fixture(t);
  const staged = path.join(f.dir, 'state/runtime/engine/.staging', VERSION, TARGET);
  fs.cpSync(path.join(f.dir, ROOT), staged, { recursive: true });
  put(path.join(staged, '.installed-receipt.json'), JSON.stringify({ schema: 1, version: VERSION, target: TARGET, sourceCommit: COMMIT, sha256: 'a'.repeat(64) }));

  assert.equal(fs.existsSync(f.destination), false);
  const found = installedRuntime({ version: VERSION, target: TARGET });
  assert.equal(found, f.destination);
  assert.equal(fs.existsSync(f.destination), true);
  assert.equal(fs.existsSync(staged), false);
});

test('discoverHostPython detects compatible host python or returns null safely', () => {
  const result = discoverHostPython();
  if (result) {
    assert.ok(typeof result.command === 'string' && result.command.length > 0);
    assert.ok(Array.isArray(result.args));
    assert.match(result.version, /^3\.(1[0-3]|\d+)\./);
  } else {
    assert.equal(result, null);
  }
});

test('discoverHostBrowser detects system Edge or Chrome when present', () => {
  const result = discoverHostBrowser();
  if (result) {
    assert.ok(['edge', 'chrome', 'chromium', 'google-chrome', 'chromium-browser', 'microsoft-edge'].includes(result.kind));
    assert.ok(fs.existsSync(result.path));
  } else {
    assert.equal(result, null);
  }
});

