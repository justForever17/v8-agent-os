const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');
const path = require('node:path');
const { spawn } = require('node:child_process');
const test = require('node:test');

const petRoot = path.resolve(__dirname, '..');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address()));
  });
}

function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

function waitForJsonLine(stream, timeoutMs = 6000) {
  return new Promise((resolve, reject) => {
    let buffered = '';
    const timeout = setTimeout(() => reject(new Error(`JSON line timed out: ${buffered}`)), timeoutMs);
    const onData = (chunk) => {
      buffered += String(chunk);
      const newline = buffered.indexOf('\n');
      if (newline < 0) return;
      clearTimeout(timeout);
      stream.off('data', onData);
      resolve(JSON.parse(buffered.slice(0, newline)));
    };
    stream.on('data', onData);
  });
}

function waitForReady(child, instanceId) {
  return new Promise((resolve, reject) => {
    let stderr = '';
    const timeout = setTimeout(() => reject(new Error(`desktop pet server handshake timed out: ${stderr}`)), 6000);
    child.stderr.on('data', (chunk) => {
      stderr += String(chunk);
    });
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      reject(new Error(`desktop pet server exited before handshake: ${code ?? signal ?? 'unknown'} ${stderr}`));
    });
    child.on('message', (message) => {
      if (message?.type !== 'v8-desktop-server-ready') return;
      clearTimeout(timeout);
      assert.equal(message.version, 1);
      assert.equal(message.instanceId, instanceId);
      assert.equal(message.pid, child.pid);
      assert.ok(Number.isInteger(message.port) && message.port > 0);
      resolve(message);
    });
  });
}

test('desktop pet server defaults to an ephemeral loopback port and proves its instance identity', { timeout: 10_000 }, async () => {
  const instanceId = crypto.randomUUID();
  const { V8_DESKTOP_PORT: _inheritedDesktopPort, ...baseEnv } = process.env;
  const child = spawn(process.execPath, ['--import', 'tsx', 'server.ts'], {
    cwd: petRoot,
    env: {
      ...baseEnv,
      NODE_ENV: 'production',
      V8_DESKTOP_SERVER_INSTANCE_ID: instanceId,
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    windowsHide: true,
  });

  try {
    const ready = await waitForReady(child, instanceId);
    assert.notEqual(ready.port, 3000);
    const response = await fetch(`http://127.0.0.1:${ready.port}/api/pet/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      ok: true,
      service: 'v8-agent-os-desktop-pet',
      version: 1,
      instanceId,
      pid: child.pid,
      port: ready.port,
    });
    const appResponse = await fetch(`http://127.0.0.1:${ready.port}/`);
    const html = await appResponse.text();
    const metaCsp = html.match(/http-equiv="Content-Security-Policy"[\s\S]*?content="([^"]+)"/i)?.[1];
    assert.equal(metaCsp, undefined, 'production CSP must come from the verified response path');
    const responseCsp = appResponse.headers.get('content-security-policy');
    assert.match(responseCsp, new RegExp(`connect-src 'self' ws:\\/\\/127\\.0\\.0\\.1:${ready.port}`));
    assert.doesNotMatch(responseCsp, /127\.0\.0\.1:\*/);
    assert.doesNotMatch(responseCsp, /unsafe-eval/);
  } finally {
    child.removeAllListeners();
    child.kill();
  }
});

test('desktop pet server still honors an explicit fixed loopback port', { timeout: 10_000 }, async () => {
  const reservation = http.createServer();
  const reservedAddress = await listen(reservation);
  await closeServer(reservation);
  const requestedPort = reservedAddress.port;
  const instanceId = crypto.randomUUID();
  const child = spawn(process.execPath, ['--import', 'tsx', 'server.ts'], {
    cwd: petRoot,
    env: {
      ...process.env,
      NODE_ENV: 'production',
      V8_DESKTOP_PORT: String(requestedPort),
      V8_DESKTOP_SERVER_INSTANCE_ID: instanceId,
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    windowsHide: true,
  });

  try {
    const ready = await waitForReady(child, instanceId);
    assert.equal(ready.port, requestedPort);
    assert.equal((await fetch(`http://127.0.0.1:${ready.port}/api/pet/health`)).status, 200);
  } finally {
    child.removeAllListeners();
    child.kill();
  }
});

test('desktop pet proxy aborts an upstream event stream when its renderer disconnects', { timeout: 10_000 }, async () => {
  let resolveUpstreamClosed;
  const upstreamClosed = new Promise((resolve) => {
    resolveUpstreamClosed = resolve;
  });
  const upstream = http.createServer((req, res) => {
    assert.equal(req.url, '/api/client/realtime/session-activity/stream');
    res.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    });
    res.write('event: ready\ndata: {}\n\n');
    req.once('close', () => resolveUpstreamClosed());
  });
  const upstreamAddress = await listen(upstream);
  const instanceId = crypto.randomUUID();
  const child = spawn(process.execPath, ['--import', 'tsx', 'server.ts'], {
    cwd: petRoot,
    env: {
      ...process.env,
      NODE_ENV: 'production',
      V8_DESKTOP_PORT: '0',
      V8_DESKTOP_SERVER_INSTANCE_ID: instanceId,
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    windowsHide: true,
  });

  try {
    const ready = await waitForReady(child, instanceId);
    const controller = new AbortController();
    const response = await fetch(
      `http://127.0.0.1:${ready.port}/api/v8/api/client/realtime/session-activity/stream`,
      {
        headers: { 'x-v8-admin-base': `http://127.0.0.1:${upstreamAddress.port}` },
        signal: controller.signal,
      },
    );
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    const firstChunk = await reader.read();
    assert.equal(firstChunk.done, false);
    controller.abort();
    await Promise.race([
      upstreamClosed,
      new Promise((_, reject) => setTimeout(() => reject(new Error('upstream SSE was not closed')), 2000)),
    ]);
  } finally {
    child.removeAllListeners();
    child.kill();
    await closeServer(upstream);
  }
});

test('desktop pet server exits and releases its port when the Electron parent disappears', { timeout: 12_000 }, async () => {
  const instanceId = crypto.randomUUID();
  const parentScript = `
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, ['--import', 'tsx', 'server.ts'], {
      cwd: ${JSON.stringify(petRoot)},
      env: { ...process.env, NODE_ENV: 'production', V8_DESKTOP_PORT: '0', V8_DESKTOP_SERVER_INSTANCE_ID: ${JSON.stringify(instanceId)} },
      stdio: ['ignore', 'ignore', 'pipe', 'ipc'],
      windowsHide: true,
    });
    child.on('message', (message) => {
      if (message?.type === 'v8-desktop-server-ready') {
        process.stdout.write(JSON.stringify({ ...message, serverPid: child.pid }) + '\\n');
      }
    });
    setInterval(() => {}, 1000);
  `;
  const parent = spawn(process.execPath, ['-e', parentScript], {
    cwd: petRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  let ready;

  try {
    ready = await waitForJsonLine(parent.stdout);
    const healthUrl = `http://127.0.0.1:${ready.port}/api/pet/health`;
    assert.equal((await fetch(healthUrl)).status, 200);
    parent.kill();

    const deadline = Date.now() + 4000;
    let released = false;
    while (Date.now() < deadline) {
      try {
        await fetch(healthUrl, { signal: AbortSignal.timeout(250) });
      } catch {
        released = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 75));
    }
    assert.equal(released, true, `orphan desktop pet server ${ready.serverPid} retained port ${ready.port}`);
  } finally {
    if (parent.exitCode === null) parent.kill();
    if (ready?.serverPid && process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(ready.serverPid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
    }
  }
});
