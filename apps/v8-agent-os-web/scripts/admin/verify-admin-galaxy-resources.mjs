import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { adminExperienceFixture } from './admin-experience-fixture.mjs';

const base = process.env.V8_ADMIN_EXPERIENCE_URL || 'http://127.0.0.1:9527';
const out = path.resolve(process.env.V8_ADMIN_EXPERIENCE_OUT || '../../../.codex-tmp/admin-resources');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' } : {}) });
try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
    await page.addInitScript(() => {
        const original = CanvasRenderingContext2D.prototype.clearRect;
        const arc = CanvasRenderingContext2D.prototype.arc;
        window.__galaxyFrames = [];
        window.__galaxyFrameRadii = [];
        CanvasRenderingContext2D.prototype.clearRect = function(...args) {
            if (this.canvas.getAttribute('aria-label')?.startsWith('记忆星系')) { window.__galaxyFrames.push(performance.now()); window.__galaxyFrameRadii.push([]); }
            return original.apply(this, args);
        };
        CanvasRenderingContext2D.prototype.arc = function(...args) {
            if (this.canvas.getAttribute('aria-label')?.startsWith('记忆星系') && args[2] > 20) window.__galaxyFrameRadii.at(-1)?.push(args[2]);
            return arc.apply(this, args);
        };
    });
    await page.goto(base + '/login', { waitUntil: 'networkidle' });
    await page.locator('#login').fill('admin-experience-fixture');
    await page.locator('#password').fill('public-admin-experience-fixture');
    await page.locator('button[type=submit]').click();
    await page.waitForURL(/\/admin$/, { timeout: 60000 });
    await page.route('**/api/**', route => {
        if (new URL(route.request().url()).pathname.startsWith('/api/auth/')) return route.continue();
        const value = adminExperienceFixture(route.request().url());
        return route.fulfill(value === undefined ? { status: 503, json: { error: 'fixture_service_unavailable' } } : { json: value });
    });
    await page.goto(base + '/admin/memory?tab=graph', { waitUntil: 'networkidle' });
    const canvas = page.getByRole('img', { name: /^记忆星系/ });
    await canvas.waitFor(); await canvas.scrollIntoViewIfNeeded(); await page.mouse.move(0, 0);
    const samples = [];
    for (let i = 0; i < 5; i++) {
        const before = await page.evaluate(() => window.__galaxyFrames.length);
        await page.waitForTimeout(1000);
        samples.push((await page.evaluate(() => window.__galaxyFrames.length)) - before);
    }
    assert.ok(samples.every(value => value > 0 && value <= 35), `passive frame counts ${samples}`);
    await page.getByRole('button', { name: /Workspace A.*12/ }).click();
    await canvas.scrollIntoViewIfNeeded(); await page.mouse.move(0, 0); await page.waitForTimeout(500);
    const radiusBefore = await page.evaluate(() => window.__galaxyFrameRadii.at(-1)[0]);
    await page.evaluate(() => { window.__galaxyFrameRadii = []; });
    const canvasBox = await canvas.boundingBox();
    await page.mouse.click(canvasBox.x + canvasBox.width - 8, canvasBox.y + 8);
    await page.mouse.move(0, 0); await page.waitForTimeout(650);
    const radii = await page.evaluate(() => window.__galaxyFrameRadii.map(frame => frame[0]).filter(Number.isFinite));
    const radiusAfter = radii.at(-1);
    const intermediateFrames = radii.filter(radius => radius < radiusBefore - 1 && radius > radiusAfter + 1).length;
    assert.ok(radiusBefore > radiusAfter && intermediateFrames >= 3, `camera return must ease through intermediate frames: ${JSON.stringify({ radiusBefore, radiusAfter, intermediateFrames, radii })}`);
    await page.getByRole('button', { name: '暂停运动', exact: true }).click();
    await page.waitForTimeout(400);
    const paused = await page.evaluate(() => window.__galaxyFrames.length);
    await page.waitForTimeout(600); assert.equal(await page.evaluate(() => window.__galaxyFrames.length), paused);
    await page.getByRole('button', { name: '继续运动', exact: true }).click();
    await page.mouse.move(0, 0); await page.waitForTimeout(400);
    // Inject the browser visibility boundary, not the component's frame function.
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
    const hidden = await page.evaluate(() => window.__galaxyFrames.length);
    await page.waitForTimeout(600); assert.equal(await page.evaluate(() => window.__galaxyFrames.length), hidden);
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(500);
    const reduced = await page.evaluate(() => window.__galaxyFrames.length);
    await page.waitForTimeout(600); assert.equal(await page.evaluate(() => window.__galaxyFrames.length), reduced);
    await page.getByRole('button', { name: /Workspace A.*12/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    assert.equal(await page.getByRole('region', { name: '节点管理' }).count(), 1);
    await page.keyboard.press('Escape');
    await page.locator('a[href="/admin/memory?tab=preferences"]').click();
    await canvas.waitFor({ state: 'detached' });
    const inactive = await page.evaluate(() => window.__galaxyFrames.length);
    await page.waitForTimeout(600); assert.equal(await page.evaluate(() => window.__galaxyFrames.length), inactive);
    for (let i = 0; i < 20; i++) {
        await page.locator('a[href="/admin/memory?tab=graph"]').click(); await canvas.waitFor();
        await page.locator('a[href="/admin/memory?tab=preferences"]').click(); await canvas.waitFor({ state: 'detached' });
    }
    const finalCount = await page.evaluate(() => window.__galaxyFrames.length);
    await page.waitForTimeout(600); assert.equal(await page.evaluate(() => window.__galaxyFrames.length), finalCount);
    const evidence = { runtime: 'real Admin canvas with synthetic HTTP data', buildMode: process.env.V8_ADMIN_BUILD_MODE || 'unspecified', browser: browser.version(), viewport: '1440x900', nodes: 36, edges: 33, samplesPerSecond: samples,
        pausedNoDraw: true, injectedHiddenNoDraw: true, reducedNoDraw: true, inactiveNoDrawAfter20Cycles: true,
        cameraReturn: { radiusBefore, radiusAfter, intermediateFrames },
        limits: 'Visibility is injected at document boundary. This is not physical background/heap or end-user INP evidence.' };
    fs.writeFileSync(path.join(out, 'resource-evidence.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence));
} finally { await browser.close(); }
