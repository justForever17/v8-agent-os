// Isolated real-browser regression harness; no product profile or user media is changed.
// Run explicitly: node tests/personalization_playback_live.mjs --live
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

if (!process.argv.includes('--live')) throw new Error('Explicit --live required for browser verification');
const require = createRequire(import.meta.url);
const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(webRoot, '../..');
const { webpack } = require(path.join(webRoot, 'node_modules/next/dist/compiled/webpack/webpack'));
const { loadBindings } = require(path.join(webRoot, 'node_modules/next/dist/build/swc'));
const { chromium } = require(path.join(repoRoot, 'apps/v8-agent-os-admin/node_modules/playwright'));
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'v8-wallpaper-fixture-'));
let browser;
let server;

try {
  fs.writeFileSync(path.join(tempRoot, 'profile.tsx'), `
import React, { createContext, useContext, useState } from 'react';
const Context = createContext(null);
export const useClientProfile = () => useContext(Context);
export function FixtureProfile({ children }) {
  const [profile, setProfile] = useState({ name: 'fixture', appearance: {
    lightBackgroundMedia: '/user-assets/background/fixture.mp4',
    lightBackgroundMediaType: 'video', lightBackgroundEnabled: true,
  }});
  window.__setFixtureProfile = setProfile;
  return <Context.Provider value={{ profile, canonicalLoaded: true }}>{children}</Context.Provider>;
}
`);
  fs.writeFileSync(path.join(tempRoot, 'entry.tsx'), `
import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { FixtureProfile } from '@fixture-profile';
import { PersonalizationProvider, useBackgroundVideoAudio } from '@fixture-provider';
import { LocaleProvider } from '@/components/providers/LocaleProvider';
import { ThinkingCard } from '@/components/chat/ThinkingCard';
import { applyRealtimeEventToMessages, normalizeSessionRuntimeEvent } from '@v8/session-realtime';
function Status() {
  const audio = useBackgroundVideoAudio();
  return <output id="available">{String(audio.available)}</output>;
}
function ThinkingFixture() {
  const owner = useRef({ messages: [], current: undefined, profile: {}, startTime: Date.now(), seq: 0 });
  const [node, setNode] = useState(null);
  useEffect(() => {
    window.__emitReasoning = envelope => {
      const state = owner.current;
      const elapsed = Date.now() - state.startTime;
      const payload = { type: 'reasoning_chunk', content: 'Thinking step ', startTime: state.startTime, durationMs: elapsed,
        message_id: 'fixture-assistant', node_id: 'fixture-reasoning' };
      const frame = { run_id: 'fixture-run', seq: ++state.seq, ts: new Date().toISOString() };
      const event = normalizeSessionRuntimeEvent(envelope ? { topic: 'run.reasoning.delta', ...frame, payload } : { ...payload, ...frame });
      const result = applyRealtimeEventToMessages(event, state.messages, state.current, state.profile);
      state.current = result.currentAiMsg;
      state.profile = result.activeAgentProfile;
      setNode({ ...result.currentAiMsg.nodes[0] });
    };
    window.__emitReasoning(false);
    return () => { delete window.__emitReasoning; };
  }, []);
  return <section id="thinking">{node && <ThinkingCard content={node.content} isStreaming elapsedTime={node.time} data={{ startTime: node.startTime }} />}</section>;
}
createRoot(document.getElementById('root')).render(<LocaleProvider initialLocale="zh-CN"><FixtureProfile><PersonalizationProvider>
  <div id="bubble" className="backdrop-blur-xl">Chat message</div>
  <div id="viewport" className="md:backdrop-blur-sm">Chat viewport</div>
  <div id="popover" className="bg-popover">Settings</div><Status /><ThinkingFixture />
</PersonalizationProvider></FixtureProfile></LocaleProvider>);
`);
  await loadBindings();
  await new Promise((resolve, reject) => webpack({
    mode: 'production', target: 'web', entry: path.join(tempRoot, 'entry.tsx'),
    output: { path: path.join(tempRoot, 'dist'), filename: 'fixture.js' },
    resolve: {
      extensions: ['.tsx', '.ts', '.js'], modules: [path.join(webRoot, 'node_modules'), 'node_modules'],
      alias: {
        '@fixture-provider': path.join(webRoot, 'src/components/providers/PersonalizationProvider.tsx'),
        '@fixture-profile': path.join(tempRoot, 'profile.tsx'),
        '@/hooks/use-client-profile': path.join(tempRoot, 'profile.tsx'),
        '@': path.join(webRoot, 'src'),
      },
    },
    module: { rules: [{ test: /\.[jt]sx?$/, exclude: /node_modules/, use: {
      loader: path.join(webRoot, 'node_modules/next/dist/build/webpack/loaders/next-swc-loader.js'),
      options: { isServer: false, rootDir: webRoot, pagesDir: path.join(webRoot, 'src/app'),
        appDir: path.join(webRoot, 'src/app'), hasReactRefresh: false, nextConfig: {},
        jsConfig: {}, supportedBrowsers: ['chrome 120'], swcPlugins: [] },
    } }] },
    optimization: { minimize: false },
    plugins: [new webpack.DefinePlugin({ 'process.env.NODE_ENV': JSON.stringify('production') })],
  }, (error, stats) => error || stats?.hasErrors() ? reject(error || new Error(stats.toString())) : resolve()));
  const css = fs.readFileSync(path.join(webRoot, 'src/app/globals.css'), 'utf8');
  server = http.createServer((request, response) => {
    if (request.url.startsWith('/api/user-media')) {
      // Keep the synthetic source pending; tests inject precise media failures.
      response.writeHead(200, { 'content-type': 'video/mp4' });
      response.flushHeaders();
      return;
    }
    if (request.url === '/fixture.js') {
      response.setHeader('content-type', 'text/javascript');
      return response.end(fs.readFileSync(path.join(tempRoot, 'dist/fixture.js')));
    }
    response.setHeader('content-type', 'text/html');
    response.end(`<html class="light"><head><style>
      [class*="backdrop-blur"], .bg-popover { backdrop-filter: blur(12px); }
      ${css}
    </style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch({ headless: true, executablePath: process.env.V8_EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const results = [];
  async function pageFixture() {
    const page = await browser.newPage();
    await page.addInitScript(() => {
      window.__loads = 0;
      window.__releases = 0;
      HTMLMediaElement.prototype.load = function () {
        if (this.hasAttribute('src')) window.__loads++;
        else window.__releases++;
      };
      HTMLMediaElement.prototype.play = async function () {};
      HTMLMediaElement.prototype.pause = function () {};
      window.__mediaError = (code) => {
        const video = document.querySelector('video');
        Object.defineProperty(video, 'error', { configurable: true, value: { code } });
        video.dispatchEvent(new Event('error'));
      };
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.querySelector('video')?.hasAttribute('src'));
    return page;
  }
  async function scenario(name, run) {
    const page = await pageFixture();
    try { const details = await run(page); results.push({ name, passed: true, ...(details ? { details } : {}) }); }
    catch (error) { results.push({ name, passed: false, error: error.message }); }
    finally { await page.close(); }
  }
  await scenario('video disables nested blur before its first frame, restoring normal styling when disabled', async (page) => {
    assert.deepEqual(await page.evaluate(() => ['bubble', 'viewport', 'popover'].map((id) => getComputedStyle(document.getElementById(id)).backdropFilter)), ['none', 'none', 'none']);
    await page.evaluate(() => window.__setFixtureProfile((previous) => ({ ...previous, appearance: { ...previous.appearance, lightBackgroundEnabled: false } })));
    await page.waitForFunction(() => !document.querySelector('video').hasAttribute('src'));
    assert.equal(await page.$eval('#bubble', (el) => getComputedStyle(el).backdropFilter), 'blur(12px)');
  });
  for (const code of [3, 4]) {
    await scenario(`media failure ${code} releases its source instead of repeatedly restarting the decoder`, async (page) => {
      await page.evaluate((value) => window.__mediaError(value), code);
      await page.waitForTimeout(350);
      assert.equal(await page.evaluate(() => window.__loads), 0);
      assert.equal(await page.evaluate(() => window.__releases), 1);
      assert.equal(await page.$eval('video', (el) => el.hasAttribute('src')), false);
      assert.equal(await page.$eval('#available', (el) => el.textContent), 'false');
    });
  }
  await scenario('network retries stay bounded across canplay and unrelated profile changes', async (page) => {
    for (const delay of [250, 750, 1500]) {
      const before = await page.evaluate(() => window.__loads);
      await page.evaluate(() => window.__mediaError(2));
      await page.waitForTimeout(delay + 100);
      assert.equal(await page.evaluate(() => window.__loads), before + 1);
      await page.evaluate(() => {
        document.querySelector('video').dispatchEvent(new Event('canplay'));
        window.__setFixtureProfile((previous) => ({ ...previous, name: `${previous.name}!`, appearance: { ...previous.appearance } }));
      });
    }
    await page.evaluate(() => window.__mediaError(2));
    await page.waitForTimeout(350);
    assert.equal(await page.evaluate(() => window.__loads), 3);
    assert.equal(await page.$eval('video', (el) => el.hasAttribute('src')), false);
  });
  await scenario('a source change cancels the previous source retry', async (page) => {
    await page.evaluate(() => {
      window.__mediaError(2);
      window.__setFixtureProfile((previous) => ({ ...previous, appearance: { ...previous.appearance, lightBackgroundMedia: '/user-assets/background/second.mp4' } }));
    });
    await page.waitForTimeout(350);
    assert.equal(await page.evaluate(() => window.__loads), 0);
    assert.match(await page.$eval('video', (el) => el.getAttribute('src')), /second\.mp4/);
  });
  await scenario('real ThinkingCard stays monotonic across typed and envelope reasoning every 3.1 seconds', async (page) => {
    const seconds = async () => {
      const text = await page.locator('#thinking .tabular-nums').innerText();
      return Number.parseFloat(text) / (text.endsWith('ms') ? 1000 : 1);
    };
    const samples = [];
    for (const envelope of [false, true, false]) {
      await page.waitForTimeout(3100);
      const before = await seconds();
      await page.evaluate(value => window.__emitReasoning(value), envelope);
      await page.waitForTimeout(150);
      const after = await seconds();
      samples.push({ before, after, envelope });
      assert.ok(after >= before, `visible timer reset: ${before}s -> ${after}s`);
    }
    assert.ok(samples.at(-1).after >= 9);
    return { screenSeconds: samples };
  });
  console.log(JSON.stringify({ level: 'isolated browser, synthetic media failures, production React provider and CSS', results }, null, 2));
  if (results.some((result) => !result.passed)) process.exitCode = 1;
} finally {
  await browser?.close();
  server?.closeAllConnections();
  if (server) await new Promise((resolve) => server.close(resolve));
  fs.rmSync(tempRoot, { recursive: true, force: true });
}
