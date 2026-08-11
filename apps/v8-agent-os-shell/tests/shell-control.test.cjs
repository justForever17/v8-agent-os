const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  MAX_MESSAGE_BYTES,
  createShellControlServer,
  runtimeRootPath,
  validatePetMessage,
  validateShellMessage,
} = require('../lib/shell-control.cjs');
const {
  createShellControlClient,
  defaultDescriptorPath: desktopPetDescriptorPath,
} = require('../../v8-agent-os-desktop-pet/lib/shell-control-client.cjs');

function deferred() {
  let resolve;
  const promise = new Promise((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

test('default control descriptor follows the governed V8 state root', () => {
  const previous = process.env.V8_AGENT_OS_HOME;
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-shell-state-root-'));
  try {
    process.env.V8_AGENT_OS_HOME = stateRoot;
    assert.equal(runtimeRootPath(), path.join(stateRoot, 'runtime'));
    assert.equal(desktopPetDescriptorPath(), path.join(stateRoot, 'runtime', 'shell-control.json'));
  } finally {
    if (previous === undefined) delete process.env.V8_AGENT_OS_HOME;
    else process.env.V8_AGENT_OS_HOME = previous;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test('local control channel authenticates the desktop pet and exchanges allowlisted messages', async (t) => {
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-shell-control-'));
  const authenticated = deferred();
  const clientConnected = deferred();
  const petMessage = deferred();
  const shellMessage = deferred();
  const packagedExecutable = path.join(runtimeRoot, process.platform === 'win32' ? 'V8 Agent OS.exe' : 'v8-agent-os-shell');
  const packagedRepoRoot = path.join(runtimeRoot, 'resources', 'v8os');
  const server = createShellControlServer({
    runtimeRoot,
    packaged: true,
    executablePath: packagedExecutable,
    repoRoot: packagedRepoRoot,
    onAuthenticated: authenticated.resolve,
    onMessage: petMessage.resolve,
  });
  const client = createShellControlClient({
    descriptorPath: server.descriptorPath,
    pollIntervalMs: 40,
    onConnected: clientConnected.resolve,
    onMessage: shellMessage.resolve,
  });
  t.after(async () => {
    client.stop();
    await server.stop();
    fs.rmSync(runtimeRoot, { recursive: true, force: true });
  });

  await server.start();
  const descriptor = JSON.parse(fs.readFileSync(server.descriptorPath, 'utf8'));
  assert.equal(descriptor.version, 1);
  assert.match(descriptor.token, /^[a-f0-9]{64}$/);
  assert.equal(descriptor.surfaceReady, false);
  assert.equal(descriptor.surfaceKind, null);
  assert.equal(descriptor.surfaceReadyAt, null);
  assert.equal(descriptor.packaged, true);
  assert.equal(descriptor.runtimeKind, 'shell');
  assert.equal(descriptor.executablePath, path.resolve(packagedExecutable));
  assert.equal(descriptor.repoRoot, path.resolve(packagedRepoRoot));

  client.start();
  await authenticated.promise;
  await clientConnected.promise;
  assert.equal(client.isConnected(), true);
  assert.equal(client.send('pet-status', { state: 'connected', activeSessionId: 'session-live-001' }), true);
  assert.deepEqual(await petMessage.promise, {
    type: 'pet-status',
    state: 'connected',
    activeSessionId: 'session-live-001',
  });

  assert.equal(server.send('active-session', { sessionId: 'session-live-002' }), 1);
  assert.deepEqual(await shellMessage.promise, { type: 'active-session', sessionId: 'session-live-002' });
  assert.equal(server.setRuntimeStatus({
    desktopPetState: 'connected',
    desktopPetProcessRunning: true,
    controlConnected: true,
    desktopPetActiveSessionId: 'session-live-002',
  }), true);
  const runtimeStatus = JSON.parse(fs.readFileSync(server.descriptorPath, 'utf8')).status;
  assert.equal(runtimeStatus.desktopPetState, 'connected');
  assert.equal(runtimeStatus.controlConnected, true);
  assert.equal(runtimeStatus.desktopPetActiveSessionId, 'session-live-002');
  assert.equal('token' in runtimeStatus, false);
  assert.equal(server.setSurfaceStatus({ surfaceReady: true, surfaceKind: 'web' }), true);
  const readyDescriptor = JSON.parse(fs.readFileSync(server.descriptorPath, 'utf8'));
  assert.equal(readyDescriptor.surfaceReady, true);
  assert.equal(readyDescriptor.surfaceKind, 'web');
  assert.ok(Date.parse(readyDescriptor.surfaceReadyAt));
  assert.equal(server.setSurfaceStatus({ surfaceReady: true, surfaceKind: 'admin-login' }), true);
  const adminLoginDescriptor = JSON.parse(fs.readFileSync(server.descriptorPath, 'utf8'));
  assert.equal(adminLoginDescriptor.surfaceReady, true);
  assert.equal(adminLoginDescriptor.surfaceKind, 'admin-login');
  assert.equal(server.setSurfaceStatus({ surfaceReady: true, surfaceKind: 'startup' }), true);
  const rejectedSurface = JSON.parse(fs.readFileSync(server.descriptorPath, 'utf8'));
  assert.equal(rejectedSurface.surfaceReady, false);
  assert.equal(rejectedSurface.surfaceKind, null);
  assert.equal(rejectedSurface.surfaceReadyAt, null);
});

test('control protocol rejects unknown, oversized, and invalid session messages', () => {
  assert.equal(validatePetMessage({ type: 'open-browser', url: 'https://example.com' }), false);
  assert.equal(validatePetMessage({ type: 'open-session', sessionId: '../bad' }), false);
  assert.equal(validatePetMessage({ type: 'pet-status', state: 'connected', activeSessionId: 'session-live-003' }), true);
  assert.equal(validateShellMessage({ type: 'active-session', sessionId: 'bad' }), false);
  assert.equal(validateShellMessage({ type: 'shutdown', requestId: 'request-1' }), true);
  assert.equal(Buffer.byteLength('x'.repeat(MAX_MESSAGE_BYTES + 1)) > MAX_MESSAGE_BYTES, true);
});

test('local control channel rejects an invalid handshake token', async () => {
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-shell-auth-'));
  let authenticated = false;
  const server = createShellControlServer({
    runtimeRoot,
    onAuthenticated: () => { authenticated = true; },
  });
  await server.start();
  const descriptor = JSON.parse(fs.readFileSync(server.descriptorPath, 'utf8'));
  try {
    await new Promise((resolve, reject) => {
      const socket = net.createConnection(descriptor.endpoint);
      const timeout = setTimeout(() => reject(new Error('invalid handshake was not rejected')), 1000);
      socket.on('connect', () => {
        socket.write(`${JSON.stringify({
          type: 'hello',
          version: 1,
          role: 'desktop-pet',
          token: '0'.repeat(64),
        })}\n`);
      });
      socket.on('close', () => {
        clearTimeout(timeout);
        resolve();
      });
      socket.on('error', () => undefined);
    });
    assert.equal(authenticated, false);
  } finally {
    await server.stop();
    fs.rmSync(runtimeRoot, { recursive: true, force: true });
  }
});

test('control descriptor restores the active task after a Shell rebuild', async () => {
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8os-shell-restore-'));
  const first = createShellControlServer({ runtimeRoot });
  await first.start();
  assert.equal(first.setActiveSession('session-live-restore'), true);
  const staleDescriptor = JSON.parse(fs.readFileSync(first.descriptorPath, 'utf8'));
  await first.stop();
  fs.writeFileSync(first.descriptorPath, JSON.stringify(staleDescriptor), 'utf8');

  const second = createShellControlServer({ runtimeRoot });
  try {
    const restored = await second.start();
    assert.equal(restored.previousActiveSessionId, 'session-live-restore');
  } finally {
    await second.stop();
    fs.rmSync(runtimeRoot, { recursive: true, force: true });
  }
});
