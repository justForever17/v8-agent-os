const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const test = require('node:test');
const { WebSocketServer } = require('ws');

const petRoot = path.resolve(__dirname, '..');
const electronPath = require('electron');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address()));
  });
}

function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

async function createFixture(label) {
  const instanceId = crypto.randomUUID();
  const requests = [];
  let bypassConnections = 0;
  const bypassServer = http.createServer((_req, res) => res.end('bypass'));
  const bypassWss = new WebSocketServer({ server: bypassServer });
  bypassWss.on('connection', (socket) => {
    bypassConnections += 1;
    socket.send('bypass-opened');
  });
  const bypassAddress = await listen(bypassServer);
  const server = http.createServer((req, res) => {
    const requestUrl = new URL(req.url || '/', 'http://127.0.0.1');
    const port = server.address().port;
    if (requestUrl.pathname === '/api/pet/health') {
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ ok: true, instanceId, pid: process.pid, port }));
      return;
    }
    if (requestUrl.pathname === '/') {
      res.setHeader(
        'content-security-policy',
        "default-src 'self'; script-src 'self'; connect-src 'self' ws://127.0.0.1:*; object-src 'none'; base-uri 'self'",
      );
      res.setHeader('content-type', 'text/html; charset=utf-8');
      res.end('<!doctype html><title>stable origin</title><script src="/asset.js"></script>');
      return;
    }
    if (requestUrl.pathname === '/asset.js') {
      res.setHeader('content-type', 'text/javascript; charset=utf-8');
      res.end(`
        (async () => {
          const storageKey = 'v8.stableOrigin.marker';
          const before = localStorage.getItem(storageKey);
          if (!before) localStorage.setItem(storageKey, 'persisted-across-port-change');
          const echo = await fetch('/api/echo', {
            method: 'POST',
            headers: { 'content-type': 'text/plain' },
            body: ${JSON.stringify(label)},
          }).then((response) => response.text());
          const eventText = await fetch('/api/events').then((response) => response.text());
          const forwardedCsp = await fetch('/').then((response) => response.headers.get('content-security-policy'));
          const transport = window.v8CyberCore.transport;
          const wsUrl = new URL(transport.engineWebSocketUrl);
          wsUrl.searchParams.set('sessionId', ${JSON.stringify(label)});
          const wsMessage = await new Promise((resolve, reject) => {
            const socket = new WebSocket(wsUrl);
            const timeout = setTimeout(() => reject(new Error('websocket timeout')), 3000);
            socket.onmessage = (event) => {
              clearTimeout(timeout);
              resolve(String(event.data));
              socket.close();
            };
            socket.onerror = () => {
              clearTimeout(timeout);
              reject(new Error('websocket failed'));
            };
          });
          const bypassBlocked = await new Promise((resolve) => {
            let socket;
            let settled = false;
            const settle = (value) => {
              if (settled) return;
              settled = true;
              try { socket && socket.close(); } catch {}
              resolve(value);
            };
            try {
              socket = new WebSocket(${JSON.stringify(`ws://127.0.0.1:${bypassAddress.port}/bypass`)});
              const timeout = setTimeout(() => settle(true), 1500);
              socket.onopen = () => {
                clearTimeout(timeout);
                settle(false);
              };
              socket.onerror = () => {
                clearTimeout(timeout);
                settle(true);
              };
            } catch {
              settle(true);
            }
          });
          window.__V8_TEST_RESULT__ = {
            origin: location.origin,
            before,
            after: localStorage.getItem(storageKey),
            echo,
            eventText,
            forwardedCsp,
            wsMessage,
            bypassBlocked,
            transport,
            transportFrozen: Object.isFrozen(transport),
          };
        })().catch((error) => {
          window.__V8_TEST_ERROR__ = error && (error.stack || error.message) || String(error);
        });
      `);
      return;
    }
    if (requestUrl.pathname === '/api/echo') {
      let body = '';
      req.setEncoding('utf8');
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        requests.push({ method: req.method, path: requestUrl.pathname, body });
        res.setHeader('content-type', 'text/plain');
        res.end(body);
      });
      return;
    }
    if (requestUrl.pathname === '/api/events') {
      res.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
      });
      res.write(`event: fixture\ndata: ${label}-first\n\n`);
      setTimeout(() => res.end(`event: fixture\ndata: ${label}-second\n\n`), 40);
      return;
    }
    res.statusCode = 404;
    res.end('not found');
  });
  const wss = new WebSocketServer({ noServer: true });
  server.on('upgrade', (request, socket, head) => {
    const requestUrl = new URL(request.url || '/', 'http://127.0.0.1');
    if (requestUrl.pathname !== '/api/v8/engine-ws') {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(request, socket, head, (webSocket) => {
      webSocket.send(`ws:${label}:${requestUrl.searchParams.get('sessionId') || ''}`);
    });
  });
  const address = await listen(server);
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    instanceId,
    requests,
    get bypassConnections() {
      return bypassConnections;
    },
    async close() {
      for (const client of wss.clients) client.terminate();
      wss.close();
      await closeServer(server);
      for (const client of bypassWss.clients) client.terminate();
      bypassWss.close();
      await closeServer(bypassServer);
    },
  };
}

function runElectronHarness(fixture, userDataPath, resultPath) {
  return new Promise((resolve, reject) => {
    const {
      ELECTRON_RUN_AS_NODE: _electronRunAsNode,
      V8_DESKTOP_DEV_SERVER: _devServer,
      ...baseEnv
    } = process.env;
    const child = spawn(electronPath, [path.join('test', 'electron-stable-origin-harness.cjs')], {
      cwd: petRoot,
      env: {
        ...baseEnv,
        V8_TEST_USER_DATA: userDataPath,
        V8_TEST_RESULT_PATH: resultPath,
        V8_TEST_BACKEND_BASE: fixture.baseUrl,
        V8_TEST_INSTANCE_ID: fixture.instanceId,
        V8_TEST_SERVER_PID: String(process.pid),
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error(`Electron stable-origin harness timed out\n${stdout}\n${stderr}`));
    }, 15_000);
    child.stdout.on('data', (chunk) => { stdout += String(chunk); });
    child.stderr.on('data', (chunk) => { stderr += String(chunk); });
    child.once('error', reject);
    child.once('exit', (code) => {
      clearTimeout(timeout);
      if (code !== 0) {
        reject(new Error(`Electron stable-origin harness exited ${code}\n${stdout}\n${stderr}`));
        return;
      }
      const result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
      if (result.error) {
        reject(new Error(result.error));
        return;
      }
      resolve(result);
    });
  });
}

test('Electron keeps one stable renderer origin across two different loopback ports', { timeout: 40_000 }, async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-pet-stable-origin-'));
  const userDataPath = path.join(tempRoot, 'user-data');
  const firstFixture = await createFixture('first');
  const secondFixture = await createFixture('second');
  try {
    assert.notEqual(firstFixture.baseUrl, secondFixture.baseUrl);
    const first = await runElectronHarness(firstFixture, userDataPath, path.join(tempRoot, 'first.json'));
    const second = await runElectronHarness(secondFixture, userDataPath, path.join(tempRoot, 'second.json'));

    assert.equal(first.origin, 'v8-desktop://app');
    assert.equal(second.origin, 'v8-desktop://app');
    assert.equal(first.before, null);
    assert.equal(first.after, 'persisted-across-port-change');
    assert.equal(second.before, 'persisted-across-port-change');
    assert.equal(second.after, 'persisted-across-port-change');
    assert.equal(first.echo, 'first');
    assert.equal(second.echo, 'second');
    assert.match(first.eventText, /first-first[\s\S]*first-second/);
    assert.match(second.eventText, /second-first[\s\S]*second-second/);
    assert.equal(first.wsMessage, 'ws:first:first');
    assert.equal(second.wsMessage, 'ws:second:second');
    assert.equal(first.bypassBlocked, true);
    assert.equal(second.bypassBlocked, true);
    assert.equal(firstFixture.bypassConnections, 0);
    assert.equal(secondFixture.bypassConnections, 0);
    assert.match(first.forwardedCsp, new RegExp(`connect-src 'self' ${firstFixture.baseUrl.replace('http:', 'ws:')}`));
    assert.match(second.forwardedCsp, new RegExp(`connect-src 'self' ${secondFixture.baseUrl.replace('http:', 'ws:')}`));
    assert.doesNotMatch(first.forwardedCsp, /127\.0\.0\.1:\*/);
    assert.doesNotMatch(second.forwardedCsp, /127\.0\.0\.1:\*/);
    assert.equal(new URL(first.transport.engineWebSocketUrl).origin, firstFixture.baseUrl.replace('http:', 'ws:'));
    assert.equal(new URL(second.transport.engineWebSocketUrl).origin, secondFixture.baseUrl.replace('http:', 'ws:'));
    assert.equal('httpBaseUrl' in first.transport, false);
    assert.equal('httpBaseUrl' in second.transport, false);
    assert.equal(first.transportFrozen, true);
    assert.equal(second.transportFrozen, true);
    assert.deepEqual(firstFixture.requests, [{ method: 'POST', path: '/api/echo', body: 'first' }]);
    assert.deepEqual(secondFixture.requests, [{ method: 'POST', path: '/api/echo', body: 'second' }]);
  } finally {
    await firstFixture.close();
    await secondFixture.close();
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});
