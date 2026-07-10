const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { createShellLifecycleWatchdog, isRestartLeaseActive } = require('../lib/shell-lifecycle-watchdog.cjs');

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test('restart leases require both an unexpired deadline and a live preview owner', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-pet-restart-lease-'));
  const filePath = path.join(root, 'shell-restart.json');
  try {
    fs.writeFileSync(filePath, JSON.stringify({
      version: 1,
      reason: 'preview_rebuild',
      ownerPid: process.pid,
      expiresAt: 2_000,
    }));
    assert.equal(isRestartLeaseActive(filePath, 1_000), true);
    assert.equal(isRestartLeaseActive(filePath, 2_001), false);
    fs.writeFileSync(filePath, JSON.stringify({
      version: 1,
      reason: 'preview_rebuild',
      ownerPid: 999_999_999,
      expiresAt: 2_000,
    }));
    assert.equal(isRestartLeaseActive(filePath, 1_000), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('managed desktop pet exits after the Shell descriptor disappears without a restart lease', async () => {
  const events = [];
  const watchdog = createShellLifecycleWatchdog({
    graceMs: 100,
    pollIntervalMs: 10,
    readDescriptor: () => null,
    isRestartLeaseActive: () => false,
    isControlConnected: () => false,
    onShellUnavailable: (event) => events.push(event),
  });
  watchdog.markDisconnected();
  await wait(160);
  watchdog.stop();
  assert.deepEqual(events, [{ reason: 'shell_descriptor_missing', shellPid: null }]);
});

test('preview rebuild lease keeps the desktop pet alive until a replacement Shell connects', async () => {
  const events = [];
  let leaseActive = true;
  let connected = false;
  const watchdog = createShellLifecycleWatchdog({
    graceMs: 20,
    pollIntervalMs: 10,
    readDescriptor: () => null,
    isRestartLeaseActive: () => leaseActive,
    isControlConnected: () => connected,
    onShellUnavailable: (event) => events.push(event),
  });
  watchdog.markDisconnected();
  await wait(50);
  assert.equal(events.length, 0);
  connected = true;
  leaseActive = false;
  watchdog.markConnected();
  await wait(30);
  watchdog.stop();
  assert.equal(events.length, 0);
});

test('a live Shell process tolerates a transient control channel disconnect', async () => {
  const events = [];
  const watchdog = createShellLifecycleWatchdog({
    graceMs: 20,
    pollIntervalMs: 10,
    readDescriptor: () => ({ pid: 42 }),
    isProcessAlive: () => true,
    isRestartLeaseActive: () => false,
    isControlConnected: () => false,
    onShellUnavailable: (event) => events.push(event),
  });
  watchdog.markDisconnected();
  await wait(60);
  watchdog.stop();
  assert.equal(events.length, 0);
});
