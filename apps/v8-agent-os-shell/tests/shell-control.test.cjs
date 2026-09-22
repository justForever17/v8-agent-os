const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { createShellState, isValidSessionId } = require('../lib/shell-control.cjs');

test('Shell persists readiness and companion projection without opening a second control transport', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-shell-state-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const state = createShellState({ runtimeRoot: root });
  const started = await state.start();
  assert.equal(started.version, 2);
  assert.equal(started.endpoint, undefined);
  assert.equal(started.token, undefined);
  state.setActiveSession('session-convergence-1');
  state.setSurfaceStatus({ surfaceReady: true, surfaceKind: 'admin' });
  state.setRuntimeStatus({ desktopPetState: 'connected', desktopPetProcessRunning: true });
  const saved = JSON.parse(fs.readFileSync(state.descriptorPath));
  assert.equal(saved.activeSessionId, 'session-convergence-1');
  assert.equal(saved.surfaceReady, true);
  assert.equal(saved.status.desktopPetState, 'connected');
  assert.equal(isValidSessionId('../outside'), false);
  await state.stop();
  assert.equal(fs.existsSync(state.descriptorPath), false);
});

test('Shell restores the selected task and never removes a replacement owner descriptor', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-shell-state-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = createShellState({ runtimeRoot: root });
  await first.start();
  first.setActiveSession('session-restore-1');
  const second = createShellState({ runtimeRoot: root });
  const restored = await second.start();
  assert.equal(restored.previousActiveSessionId, 'session-restore-1');
  const replacement = fs.readFileSync(second.descriptorPath, 'utf8');
  await first.stop();
  assert.equal(fs.readFileSync(second.descriptorPath, 'utf8'), replacement);
  await second.stop();
  assert.equal(fs.existsSync(second.descriptorPath), false);
});
