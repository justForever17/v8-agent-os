#!/usr/bin/env node

import http from 'node:http';
import { URL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';

const PORT = parseInt(process.env.CDP_PROXY_PORT || '3456', 10);
const PINNED_TARGET_PORT = parseInt(process.env.CDP_TARGET_PORT || '0', 10) || null;
let ws = null;
let cmdId = 0;
const pending = new Map();
const sessions = new Map();

let WS;
if (typeof globalThis.WebSocket !== 'undefined') {
  WS = globalThis.WebSocket;
} else {
  try {
    WS = (await import('ws')).default;
  } catch {
    console.error('[browser_cdp_proxy] 需要 Node.js 22+ 或已安装 ws。');
    process.exit(1);
  }
}

async function checkPort(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection(port, '127.0.0.1');
    const timer = setTimeout(() => { socket.destroy(); resolve(false); }, 1500);
    socket.once('connect', () => { clearTimeout(timer); socket.destroy(); resolve(true); });
    socket.once('error', () => { clearTimeout(timer); resolve(false); });
  });
}

async function fetchVersionInfo(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function discoverChromePort() {
  if (PINNED_TARGET_PORT) {
    const ok = await checkPort(PINNED_TARGET_PORT);
    if (ok) {
      const version = await fetchVersionInfo(PINNED_TARGET_PORT);
      const wsUrl = typeof version?.webSocketDebuggerUrl === 'string' ? version.webSocketDebuggerUrl : '';
      const wsPath = wsUrl ? new URL(wsUrl).pathname : null;
      return { port: PINNED_TARGET_PORT, wsPath };
    }
  }
  const possiblePaths = [];
  if (os.platform() === 'win32') {
    const localAppData = process.env.LOCALAPPDATA || '';
    possiblePaths.push(
      path.join(localAppData, 'Google/Chrome/User Data/DevToolsActivePort'),
      path.join(localAppData, 'Microsoft/Edge/User Data/DevToolsActivePort'),
      path.join(localAppData, 'Chromium/User Data/DevToolsActivePort'),
    );
  }
  for (const filePath of possiblePaths) {
    try {
      const lines = fs.readFileSync(filePath, 'utf-8').trim().split('\n');
      const port = parseInt(lines[0], 10);
      if (port > 0 && await checkPort(port)) {
        const version = await fetchVersionInfo(port);
        const wsUrl = typeof version?.webSocketDebuggerUrl === 'string' ? version.webSocketDebuggerUrl : '';
        return { port, wsPath: wsUrl ? new URL(wsUrl).pathname : (lines[1] || null) };
      }
    } catch {}
  }
  for (const port of [9222, 9229, 9333, PORT + 100]) {
    if (await checkPort(port)) {
      const version = await fetchVersionInfo(port);
      const wsUrl = typeof version?.webSocketDebuggerUrl === 'string' ? version.webSocketDebuggerUrl : '';
      return { port, wsPath: wsUrl ? new URL(wsUrl).pathname : null };
    }
  }
  return null;
}

let chromePort = null;
let chromeWsPath = null;
let connectingPromise = null;

function getWebSocketUrl(port, wsPath) {
  if (wsPath) return `ws://127.0.0.1:${port}${wsPath}`;
  return `ws://127.0.0.1:${port}/devtools/browser`;
}

async function connect() {
  if (ws && (ws.readyState === WS.OPEN || ws.readyState === 1)) return;
  if (connectingPromise) return connectingPromise;
  const discovered = await discoverChromePort();
  if (!discovered) throw new Error('未发现可用的 Chromium DevTools 端口。');
  chromePort = discovered.port;
  chromeWsPath = discovered.wsPath;
  const wsUrl = getWebSocketUrl(chromePort, chromeWsPath);
  connectingPromise = new Promise((resolve, reject) => {
    ws = new WS(wsUrl);
    const onOpen = () => { cleanup(); connectingPromise = null; resolve(); };
    const onError = (e) => { cleanup(); connectingPromise = null; ws = null; reject(e); };
    const onClose = () => { ws = null; sessions.clear(); chromePort = null; chromeWsPath = null; };
    const onMessage = (evt) => {
      const data = typeof evt === 'string' ? evt : (evt.data || evt);
      const msg = JSON.parse(typeof data === 'string' ? data : data.toString());
      if (msg.method === 'Target.attachedToTarget') {
        const { sessionId, targetInfo } = msg.params;
        sessions.set(targetInfo.targetId, sessionId);
      }
      if (msg.id && pending.has(msg.id)) {
        const { resolve, timer } = pending.get(msg.id);
        clearTimeout(timer);
        pending.delete(msg.id);
        resolve(msg);
      }
    };
    function cleanup() {
      ws.removeEventListener?.('open', onOpen);
      ws.removeEventListener?.('error', onError);
    }
    if (ws.on) {
      ws.on('open', onOpen);
      ws.on('error', onError);
      ws.on('close', onClose);
      ws.on('message', onMessage);
    } else {
      ws.addEventListener('open', onOpen);
      ws.addEventListener('error', onError);
      ws.addEventListener('close', onClose);
      ws.addEventListener('message', onMessage);
    }
  });
  return connectingPromise;
}

function sendCDP(method, params = {}, sessionId = null) {
  return new Promise((resolve, reject) => {
    if (!ws || (ws.readyState !== WS.OPEN && ws.readyState !== 1)) return reject(new Error('WebSocket 未连接'));
    const id = ++cmdId;
    const msg = { id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP 命令超时: ${method}`));
    }, 20000);
    pending.set(id, { resolve, timer });
    ws.send(JSON.stringify(msg));
  });
}

async function ensureSession(targetId) {
  if (sessions.has(targetId)) return sessions.get(targetId);
  const resp = await sendCDP('Target.attachToTarget', { targetId, flatten: true });
  if (resp.result?.sessionId) {
    sessions.set(targetId, resp.result.sessionId);
    return resp.result.sessionId;
  }
  throw new Error(`attach 失败: ${JSON.stringify(resp.error)}`);
}

async function waitForLoad(sessionId, timeoutMs = 12000) {
  await sendCDP('Page.enable', {}, sessionId);
  return new Promise((resolve) => {
    let resolved = false;
    const done = (result) => {
      if (resolved) return;
      resolved = true;
      clearTimeout(timer);
      cleanup();
      resolve(result);
    };
    const timer = setTimeout(() => done('timeout'), timeoutMs);
    const onMessage = (evt) => {
      const data = typeof evt === 'string' ? evt : (evt.data || evt);
      const msg = JSON.parse(typeof data === 'string' ? data : data.toString());
      if (msg.sessionId !== sessionId) return;
      if (msg.method === 'Page.loadEventFired') done('load');
    };
    function cleanup() {
      if (!ws) return;
      if (ws.off) ws.off('message', onMessage);
      else ws.removeEventListener?.('message', onMessage);
    }
    if (ws.on) ws.on('message', onMessage);
    else ws.addEventListener('message', onMessage);
  });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url, `http://localhost:${PORT}`);
  const pathname = parsed.pathname;
  const q = Object.fromEntries(parsed.searchParams);
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  try {
    if (pathname === '/health') {
      const connected = ws && (ws.readyState === WS.OPEN || ws.readyState === 1);
      res.end(JSON.stringify({ status: 'ok', connected, sessions: sessions.size, chromePort }));
      return;
    }
    await connect();
    if (pathname === '/targets') {
      const resp = await sendCDP('Target.getTargets');
      const pages = (resp.result?.targetInfos || []).filter((item) => item.type === 'page');
      res.end(JSON.stringify(pages, null, 2));
      return;
    }
    if (pathname === '/new') {
      const targetUrl = q.url || 'about:blank';
      const resp = await sendCDP('Target.createTarget', { url: targetUrl, background: true });
      const targetId = resp.result?.targetId;
      if (targetId && targetUrl !== 'about:blank') {
        try {
          const sid = await ensureSession(targetId);
          await waitForLoad(sid);
        } catch {}
      }
      res.end(JSON.stringify({ targetId }));
      return;
    }
    if (pathname === '/navigate') {
      const sid = await ensureSession(q.target);
      const resp = await sendCDP('Page.navigate', { url: q.url }, sid);
      await waitForLoad(sid);
      res.end(JSON.stringify(resp.result || {}));
      return;
    }
    if (pathname === '/info') {
      const sid = await ensureSession(q.target);
      const resp = await sendCDP('Runtime.evaluate', {
        expression: 'JSON.stringify({title: document.title, url: location.href, ready: document.readyState})',
        returnByValue: true,
      }, sid);
      res.end(resp.result?.result?.value || '{}');
      return;
    }
    if (pathname === '/eval') {
      const sid = await ensureSession(q.target);
      const expr = await readBody(req);
      const resp = await sendCDP('Runtime.evaluate', {
        expression: expr,
        returnByValue: true,
        awaitPromise: true,
      }, sid);
      if (resp.result?.exceptionDetails) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: resp.result.exceptionDetails.text }));
        return;
      }
      res.end(JSON.stringify({ value: resp.result?.result?.value }));
      return;
    }
    if (pathname === '/scroll') {
      const sid = await ensureSession(q.target);
      const y = parseInt(q.y || '1200', 10);
      const direction = q.direction || 'down';
      let js = `window.scrollBy(0, ${Math.abs(y)}); "scrolled down ${Math.abs(y)}px"`;
      if (direction === 'up') js = `window.scrollBy(0, -${Math.abs(y)}); "scrolled up ${Math.abs(y)}px"`;
      if (direction === 'top') js = 'window.scrollTo(0, 0); "scrolled to top"';
      if (direction === 'bottom') js = 'window.scrollTo(0, document.body.scrollHeight); "scrolled to bottom"';
      const resp = await sendCDP('Runtime.evaluate', { expression: js, returnByValue: true }, sid);
      res.end(JSON.stringify({ value: resp.result?.result?.value }));
      return;
    }
    if (pathname === '/setFiles') {
      const sid = await ensureSession(q.target);
      const body = JSON.parse(await readBody(req));
      await sendCDP('DOM.enable', {}, sid);
      const doc = await sendCDP('DOM.getDocument', {}, sid);
      const node = await sendCDP('DOM.querySelector', {
        nodeId: doc.result.root.nodeId,
        selector: body.selector
      }, sid);
      if (!node.result?.nodeId) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: `未找到元素: ${body.selector}` }));
        return;
      }
      await sendCDP('DOM.setFileInputFiles', {
        nodeId: node.result.nodeId,
        files: body.files || [],
      }, sid);
      res.end(JSON.stringify({ success: true, files: (body.files || []).length }));
      return;
    }
    res.statusCode = 404;
    res.end(JSON.stringify({ error: '未知端点' }));
  } catch (error) {
    res.statusCode = 500;
    res.end(JSON.stringify({ error: String(error?.message || error) }));
  }
});

server.listen(PORT, '127.0.0.1');
