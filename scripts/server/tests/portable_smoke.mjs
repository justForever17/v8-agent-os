#!/usr/bin/env node
// Real package/lifecycle/browser smoke. Run only in the disposable CI container.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

if (!process.argv.includes('--live')) throw new Error('Explicit --live required for package/process/browser acceptance');
if (process.platform !== 'linux' || process.arch !== 'x64') throw new Error('This acceptance covers Linux x64 only');
const state = process.env.V8_AGENT_OS_HOME;
assert(state?.startsWith('/home/server-test/'), 'Use only the disposable container state');
assert(!fs.existsSync(state), 'First-run acceptance requires empty state');
for (const executable of ['/usr/bin/python', '/usr/bin/python3', '/usr/local/bin/python', '/usr/local/bin/python3']) {
  assert(!fs.existsSync(executable), `Unexpected host Python: ${executable}`);
}
const manifests = fs.readdirSync('/opt/assets').filter(name => /^V8OS-Engine-.*-linux-x64\.json$/.test(name));
assert.equal(manifests.length, 1);
const manifestPath = path.join('/opt/assets', manifests[0]);
const release = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const env = {
  ...process.env,
  V8OS_ENGINE_MANIFEST_URL: pathToFileURL(manifestPath).href,
  V8OS_ENGINE_ARCHIVE: path.join('/opt/assets', release.asset),
};
for (const key of ['V8_ENGINE_PYTHON', 'V8_ENGINE_DIR', 'V8OS_ENGINE_RUNTIME_DIR', 'V8OS_ENGINE_RUNTIME_ROOT', 'PYTHONPATH', 'PYTHONHOME', 'DISPLAY', 'WAYLAND_DISPLAY']) delete env[key];
const cli = '/opt/npm/bin/v8os';
function command(...args) {
  return JSON.parse(execFileSync(cli, [...args, '--json'], { env, encoding: 'utf8', timeout: 240_000, maxBuffer: 4 * 1024 * 1024 }).trim());
}
async function health(baseUrl) {
  const result = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(10_000), redirect: 'error' });
  assert.equal(result.status, 200);
  return result.json();
}

let started = false;
try {
  assert.equal(command('status').status, 'not_installed');
  const first = command('start');
  started = true;
  const current = command('status');
  assert.equal(current.status, 'running');
  const runtime = first.root || current.runtimeRoot;
  assert(runtime && path.resolve(runtime).startsWith(`${state}/`), 'npm must install in its owned state; not use the CI build tree');
  const engine = path.join(runtime, 'apps/v8-agent-os-engine');
  const python = path.join(engine, '.python/bin/python3');
  const pip = execFileSync(path.join(engine, '.python/bin/pip'), ['--version'], { env, encoding: 'utf8' }).trim();
  assert.equal(pip, execFileSync(python, ['-m', 'pip', '--version'], { env, encoding: 'utf8' }).trim());
  assert(pip.includes(engine), 'Console scripts must use the relocated runtime');
  assert.match(execFileSync(path.join(engine, '.python/bin/playwright'), ['--version'], { env, encoding: 'utf8' }), /^Version /);
  const before = await health(current.baseUrl);
  assert.equal(before.service, 'v8-agent-os-engine');
  assert.equal(before.startupProfile, 'server');
  assert.equal(before.engineRuntime.managedRuntimeRoot, path.join(engine, '.python'));
  assert.equal(before.engineRuntime.reload, false);
  assert.equal(before.engineRuntime.interpreterDrift, false);
  assert.equal(command('start').status, 'already_running');
  const workspace = path.join(state, 'acceptance workspace');
  const created = command('workspace', 'create', workspace, '--select');
  assert.equal(created.trust.registered, true, 'Real Engine must accept the authenticated workspace request');
  assert(fs.existsSync(path.join(workspace, '.agents/rules/AGENTS.md')));
  assert.equal(command('workspace', 'show').path, workspace);
  const browser = execFileSync(python, [path.join(runtime, 'scripts/server/verify_server.py'), '--bundle', runtime, '--browser'], {
    env: { ...env, PLAYWRIGHT_BROWSERS_PATH: path.join(engine, '.playwright-browsers'), PYTHONDONTWRITEBYTECODE: '1' },
    encoding: 'utf8', timeout: 180_000, maxBuffer: 4 * 1024 * 1024,
  });
  assert(browser.includes('"javascript": true'), 'Headless Chromium must execute the page JS via the real Agent browser owner');
  const database = path.join(state, 'state.db');
  assert(fs.statSync(database).size > 0);
  const integrity = execFileSync(python, ['-I', '-c',
    'import sqlite3,sys; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; assert c.execute("SELECT count(*) FROM sqlite_master WHERE type=\'table\'").fetchone()[0]>0; print("SQLITE_PERSISTENCE_OK")', database],
  { env, encoding: 'utf8' });
  assert(integrity.includes('SQLITE_PERSISTENCE_OK'));
  command('stop');
  started = false;
  assert.notEqual(command('status').status, 'running');
  command('start', '--no-install');
  started = true;
  assert.equal(command('workspace', 'show').path, workspace, 'Restart must retain selected workspace and user data');
  assert.equal((await health(command('status').baseUrl)).engineRuntime.managedRuntimeRoot, path.join(engine, '.python'));
  for (const port of [9527, 9528]) {
    await assert.rejects(fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1_000) }), 'Admin/Web must remain absent');
  }
  console.log(JSON.stringify({ acceptance: 'portable-engine', version: release.version, sourceCommit: release.sourceCommit,
    hostPython: false, offlineRuntime: true, npmFirstStart: true, profile: 'server', duplicateStart: true, consoleRelocation: true,
    authenticatedWorkspace: true, sqliteIntegrity: true, browserJavascript: true, restartPersistence: true,
    adminAndWebAbsent: true }));
} finally {
  if (started) command('stop');
}
