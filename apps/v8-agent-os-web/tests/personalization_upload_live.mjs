// Real production Admin/Web/Engine + real video; isolated owner, ports and state.
// node tests/personalization_upload_live.mjs --live --video <read-only MP4>
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import crypto from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(webRoot, '../..');
const value = name => process.argv[process.argv.indexOf(name) + 1];
assert.ok(process.argv.includes('--live') && process.argv.includes('--video'), 'Explicit --live and --video required');
const videoPath = path.resolve(value('--video'));
assert.ok(fs.statSync(videoPath).isFile() && /\.mp4$/i.test(videoPath));
const { chromium } = require(path.join(repoRoot, 'apps/v8-agent-os-admin/node_modules/playwright'));
const { ensureManagedAuthSecret } = await import(pathToFileURL(path.join(repoRoot, 'scripts/ensure-admin-auth-secret.mjs')).href);
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-personalization-upload-'));
const children = [];
const logs = [];
let browser;
let stage = 'prepare';
const report = { level: 'production source Preview, real upload and real browser decoder, isolated owner', mediaBytes: fs.statSync(videoPath).size, pageErrors: [], crashes: 0 };

async function freePort() {
  const listener = net.createServer();
  await new Promise((resolve, reject) => { listener.once('error', reject); listener.listen(0, '127.0.0.1', resolve); });
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return port;
}
const ports = { engine: await freePort(), admin: await freePort(), web: await freePort() };
const origins = Object.fromEntries(Object.entries(ports).map(([key, port]) => [key, `http://127.0.0.1:${port}`]));

function start(command, args, cwd, extraEnv) {
  const child = spawn(command, args, { cwd, windowsHide: true, env: { ...process.env, V8_AGENT_OS_HOME: root, ...extraEnv }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', chunk => { logs.push(String(chunk)); if (logs.length > 80) logs.shift(); });
  child.stderr.on('data', chunk => { logs.push(String(chunk)); if (logs.length > 80) logs.shift(); });
  children.push(child);
  return child;
}
async function ready(url) {
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    try { if ((await fetch(url, { signal: AbortSignal.timeout(1000) })).ok) return; } catch {}
    if (children.some(child => child.exitCode !== null)) throw new Error('isolated service exited before readiness');
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  throw new Error('isolated service readiness timed out');
}

try {
  fs.mkdirSync(path.join(root, 'workspace'));
  fs.writeFileSync(path.join(root, 'config.json'), JSON.stringify({ systemBase: { bridge: {
    engineBaseUrl: `${origins.engine}/v1`, engineWsBaseUrl: `${origins.engine.replace('http:', 'ws:')}/v1`,
    adminBaseUrl: `${origins.admin}/api`, webBaseUrl: origins.web,
    internalSecret: crypto.randomBytes(32).toString('hex'), allowedOrigins: Object.values(origins),
  } } }));
  ensureManagedAuthSecret({ stateRoot: root });
  const engineRoot = path.join(repoRoot, 'apps/v8-agent-os-engine');
  const python = process.platform === 'win32' ? path.join(engineRoot, '.venv/Scripts/python.exe') : path.join(engineRoot, '.venv/bin/python');
  start(python, ['main.py'], engineRoot, { ENGINE_PORT: String(ports.engine), ENGINE_HOST: '127.0.0.1', ENGINE_RELOAD: '0', V8_WEB_BASE_URL: origins.web, PYTHONIOENCODING: 'utf-8' });
  const wrapper = path.join(repoRoot, 'scripts/run-next-with-managed-auth.mjs');
  for (const app of ['admin', 'web']) start(process.execPath, [wrapper, '--app', app, '--mode', 'start', '--port', String(ports[app])], repoRoot, { V8_ADMIN_HOSTNAME: '127.0.0.1' });
  stage = 'readiness';
  await Promise.all([ready(`${origins.engine}/readyz`), ready(`${origins.admin}/login`), ready(`${origins.web}/chat`)]);
  stage = 'isolated-owner';
  const bootstrap = await fetch(`${origins.admin}/api/auth/bootstrap`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ login: 'video-fixture', name: 'Video Fixture', password: crypto.randomBytes(24).toString('hex') }) });
  assert.equal(bootstrap.status, 200);
  browser = await chromium.launch({ headless: true, executablePath: process.env.V8_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const csrf = await (await context.request.get(`${origins.web}/api/auth/csrf`)).json();
  const login = await context.request.post(`${origins.web}/api/auth/callback/credentials`, { form: {
    csrfToken: csrf.csrfToken, localSession: '1', adminBaseUrl: origins.admin, callbackUrl: `${origins.web}/chat`, json: 'true',
  } });
  assert.ok(login.ok(), 'isolated local session must authenticate');
  const session = await (await context.request.get(`${origins.web}/api/auth/session`)).json();
  assert.equal(session.user?.login, 'video-fixture');
  const connected = await context.request.post(`${origins.web}/api/connection`, { data: { adminBaseUrl: origins.admin, persist: true } });
  assert.equal(connected.status(), 200);
  const conversations = [];
  for (const title of ['Video Fixture A', 'Video Fixture B']) {
    const response = await context.request.post(`${origins.web}/api/conversations`, { data: { title, workspacePath: path.join(root, 'workspace') } });
    assert.equal(response.status(), 200, 'real Engine session creation');
    conversations.push(await response.json());
  }
  const page = await context.newPage();
  const inFlight = new Map();
  const requestPath = request => new URL(request.url()).pathname.replace(/[a-f0-9]{8}-[a-f0-9-]{27,}/ig, ':id');
  page.on('request', request => {
    if (request.url().startsWith(origins.web)) {
      inFlight.set(request, { path: requestPath(request), type: request.resourceType(), at: Date.now() });
      report.maxConcurrentSupervisorProfileRequests = Math.max(report.maxConcurrentSupervisorProfileRequests || 0,
        [...inFlight.values()].filter(item => item.path === '/api/supervisor-profile').length);
    }
  });
  page.on('requestfinished', request => inFlight.delete(request));
  page.on('requestfailed', request => inFlight.delete(request));
  page.on('pageerror', error => report.pageErrors.push(error.message.slice(0, 250)));
  page.on('crash', () => report.crashes++);
  await page.addInitScript(() => {
    window.__videoMetrics = { events: [], activeSamples: 0, inactiveSamples: 0, notReadySamples: 0, maxFrameGapMs: 0 };
    let video, last = performance.now();
    const frame = now => {
      const current = document.querySelector('.v8-personalization-wallpaper-video');
      if (current && current !== video) {
        video = current;
        for (const type of ['loadedmetadata', 'canplay', 'playing', 'waiting', 'stalled', 'emptied', 'error']) current.addEventListener(type, () => {
          window.__videoMetrics.events.push({ type, ms: Math.round(performance.now()), error: current.error?.code || null });
        });
      }
      if (window.__watchVideo) {
        const metrics = window.__videoMetrics;
        metrics.maxFrameGapMs = Math.max(metrics.maxFrameGapMs, now - last);
        // A buffering decoder can still display its last frame. Report it
        // separately instead of inventing a white screen from readyState.
        const active = current && current.hasAttribute('src') && getComputedStyle(current).opacity === '1'
          && document.documentElement.dataset.v8Wallpaper === 'active';
        if (active) metrics.activeSamples++; else metrics.inactiveSamples++;
        if (!current || current.readyState < 2) metrics.notReadySamples++;
      }
      last = now;
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
  stage = 'settings-ui';
  await page.goto(`${origins.web}/chat?id=${encodeURIComponent(conversations[0].id)}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'V', exact: true }).click();
  await page.getByRole('menuitem', { name: /设置|Settings/ }).click();
  await page.locator('input[type="file"][accept*="video/mp4"]').waitFor({ state: 'attached' });
  stage = 'real-upload';
  const started = Date.now();
  let uploadStarted = started;
  const snapshots = [];
  page.on('request', request => {
    if (request.url().endsWith('/api/user-background-upload') && request.method() === 'POST') {
      uploadStarted = Date.now();
      for (const delay of [0, 1000, 3000, 6000, 10000]) snapshots.push(setTimeout(() => {
        (report.uploadInFlight ||= []).push({ ms: Date.now() - uploadStarted,
          requests: [...inFlight.values()].map(item => ({ path: item.path, type: item.type, ageMs: Date.now() - item.at })) });
      }, delay));
    }
  });
  const uploadResponse = page.waitForResponse(response => response.url().endsWith('/api/user-background-upload') && response.request().method() === 'POST');
  await page.locator('input[type="file"][accept*="video/mp4"]').setInputFiles(videoPath);
  const uploaded = await uploadResponse;
  snapshots.forEach(clearTimeout);
  assert.equal(uploaded.status(), 200);
  await uploaded.finished();
  const timing = uploaded.request().timing();
  report.uploadNetworkTiming = timing;
  report.browserBeforeRequestMs = timing.requestStart;
  report.requestToResponseMs = timing.responseEnd - timing.requestStart;
  report.fileSelectionToResponseMs = Date.now() - started;
  report.uploadDispatchToResponseMs = Date.now() - uploadStarted;
  stage = 'real-decode';
  await page.waitForFunction(() => {
    const video = document.querySelector('.v8-personalization-wallpaper-video');
    return video && video.videoWidth > 0 && video.currentTime > 0 && !video.paused && getComputedStyle(video).opacity === '1';
  }, undefined, { timeout: 30000 });
  report.uploadDispatchToPlayingMs = Date.now() - uploadStarted;
  report.media = await page.$eval('video', video => ({ width: video.videoWidth, height: video.videoHeight, duration: video.duration, error: video.error?.code || null }));
  await page.getByRole('dialog').getByRole('button', { name: /^关闭$|^Close$/ }).first().click();
  stage = 'session-switches';
  await page.evaluate(() => { window.__watchVideo = true; });
  report.switchMs = [];
  report.clickToRouteMs = [];
  for (let i = 0; i < 12; i++) {
    const target = conversations[(i + 1) % 2];
    const before = Date.now();
    await page.evaluate(() => document.addEventListener('click', () => { window.__fixtureClickAt = performance.now(); }, { once: true, capture: true }));
    await page.getByText(target.title, { exact: true }).first().click();
    await page.waitForFunction(id => new URL(location.href).searchParams.get('id') === id, target.id);
    report.clickToRouteMs.push(await page.evaluate(() => performance.now() - window.__fixtureClickAt));
    await page.waitForTimeout(100);
    report.switchMs.push(Date.now() - before);
  }
  await page.waitForTimeout(1500);
  report.playback = await page.evaluate(() => {
    const video = document.querySelector('video'), quality = video.getVideoPlaybackQuality();
    return { ...window.__videoMetrics, currentTime: video.currentTime,
      quality: { totalFrames: quality.totalVideoFrames, droppedFrames: quality.droppedVideoFrames, corruptedFrames: quality.corruptedVideoFrames } };
  });
  assert.equal(report.crashes, 0);
  assert.equal(report.pageErrors.length, 0);
  assert.equal(report.playback.inactiveSamples, 0, 'video stays visible through SPA session changes');
  assert.ok(!report.playback.events.some(event => event.type === 'error'));
  assert.ok(report.maxConcurrentSupervisorProfileRequests <= 1, 'slow profile requests must not pile up');
  stage = 'reload';
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('video')?.currentTime > 0 && document.documentElement.dataset.v8Wallpaper === 'active');
  report.reloadRestored = true;
  report.ok = true;
} catch (error) {
  report.ok = false;
  report.failedStage = stage;
  report.error = String(error.message).replaceAll(root, '[isolated state]').replaceAll(videoPath, '[read-only media]').slice(0, 600);
  process.exitCode = 1;
} finally {
  await browser?.close();
  for (const child of children.reverse()) {
    if (child.exitCode === null && child.pid) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' });
      else child.kill('SIGTERM');
    }
  }
  report.remainingOpenPorts = [];
  for (const [component, port] of Object.entries(ports)) {
    const open = await new Promise(resolve => {
      const socket = net.createConnection({ host: '127.0.0.1', port });
      const finish = value => { socket.destroy(); resolve(value); };
      socket.setTimeout(300, () => finish(false));
      socket.once('connect', () => finish(true));
      socket.once('error', () => finish(false));
    });
    if (open) report.remainingOpenPorts.push(component);
  }
  if (report.remainingOpenPorts.length) { report.ok = false; process.exitCode = 1; }
  console.log(JSON.stringify(report, null, 2));
  fs.rmSync(root, { recursive: true, force: true });
}
