import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test, { type TestContext } from 'node:test';

const entry = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../bin/v8os.mjs');

function run(t: TestContext, args: string[]) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-entry-options-'));
  const state = path.join(parent, 'state');
  t.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  const result = spawnSync(process.execPath, [entry, ...args], {
    encoding: 'utf8',
    timeout: 10_000,
    env: {
      ...process.env,
      V8_AGENT_OS_HOME: state,
      V8OS_ENGINE_RUNTIME_ROOT: path.join(state, 'runtime', 'engine'),
      V8OS_ENGINE_MANIFEST_URL: 'https://unreachable.invalid/v8-engine.json',
      V8OS_ENGINE_ARCHIVE: path.join(state, 'missing-engine.tar.gz'),
    },
  });
  return { ...result, state };
}

test('unsupported component selection is rejected before installation or state creation', t => {
  for (const args of [
    ['start', '--all'],
    ['start', '--with', 'web'],
    ['start', '--only', 'admin'],
    ['stop', '--only', 'engine,admin'],
    ['restart', '--only'],
  ]) {
    const result = run(t, args);
    assert.notEqual(result.status, 0, args.join(' '));
    assert.match(result.stderr, /Core Base|核心|requires a value|需要参数/);
    assert.equal(fs.existsSync(result.state), false, args.join(' '));
  }
});

test('TUI rejects unknown flags and missing option values before the TTY or Engine path', t => {
  for (const args of [['--unknown'], ['tui', '--session'], ['tui', '--lang'], ['tui', '--lang', 'fr']]) {
    const result = run(t, args);
    assert.notEqual(result.status, 0, args.join(' '));
    assert.match(result.stderr, /TUI|未知|unknown|requires|需要|accepts|只接受/i, args.join(' '));
    assert.equal(fs.existsSync(result.state), false, args.join(' '));
  }
});

test('accepted Engine-only lifecycle options do not get rejected as unknown', t => {
  const start = run(t, ['start', '--only', 'engine', '--no-install']);
  assert.notEqual(start.status, 0);
  assert.match(start.stderr, /not installed|没有可用|未安装|Portable Engine/i);

  const status = run(t, ['status', '--json']);
  assert.equal(status.status, 0, status.stderr);
  assert.equal(JSON.parse(status.stdout).status, 'not_installed');

  const tui = run(t, ['tui', '--session', 'session-1', '--lang', 'en-US', '--no-install', '--json']);
  assert.equal(tui.status, 2, tui.stderr);
  assert.equal(JSON.parse(tui.stdout).error, 'tty_required');
  assert.equal(fs.existsSync(tui.state), false);
});
