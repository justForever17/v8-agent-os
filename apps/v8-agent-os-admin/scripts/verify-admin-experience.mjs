import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { adminExperienceFixture, sampleClusters } from './admin-experience-fixture.mjs';

const base = process.env.V8_ADMIN_EXPERIENCE_URL || 'http://127.0.0.1:22828';
const out = path.resolve(process.env.V8_ADMIN_EXPERIENCE_OUT || '../../../.codex-tmp/admin-experience');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' } : {}) });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const page = await context.newPage();
const errors = [], writes = [], evidence = [];
page.on('pageerror', error => errors.push(error.message));
try {
    // Public fixture password is used only against the isolated test state root.
    await page.goto(base + '/login', { waitUntil: 'networkidle' });
    await page.locator('#login').fill('admin-experience-fixture');
    if (await page.locator('#name').count()) {
        await page.locator('#name').fill('Admin fixture');
        await page.locator('#confirmPassword').fill('public-admin-experience-fixture');
    }
    await page.locator('#password').fill('public-admin-experience-fixture');
    await page.locator('button[type=submit]').click();
    await page.waitForURL(/\/admin$/, { timeout: 60000 });
    let failWrites = false;
    await page.route('**/api/**', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname.startsWith('/api/auth/')) return route.continue();
        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
            writes.push({ path: url.pathname, method: request.method(), body: request.postDataJSON() });
            if (failWrites) return route.fulfill({ status: 500, json: { error: 'fixture_failure' } });
            if (url.pathname === '/api/memory/graph') return route.fulfill({ json: { created: true, deleted: true } });
            return route.fulfill({ json: adminExperienceFixture(request.url()) || { ok: true } });
        }
        const fixture = adminExperienceFixture(request.url());
        return route.fulfill(fixture === undefined ? { status: 503, json: { error: 'fixture_service_unavailable' } } : { json: fixture });
    });
    const visit = async route => { await page.goto(base + route, { waitUntil: 'networkidle' }); await page.waitForTimeout(250); };
    await visit('/admin/system-base');
    for (const width of [1440, 768, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const save = page.locator('#admin-save-actions button').first();
        await save.waitFor();
        const box = await save.boundingBox();
        assert.ok(box && box.y >= 0 && box.y + box.height <= 900 && box.x + box.width <= width, 'save must be visible before scrolling');
        if (width < 1024) { await page.getByRole('button', { name: '导航', exact: true }).click(); await page.getByRole('dialog').waitFor(); assert.ok(await page.getByRole('dialog').getByRole('link', { name: '模型', exact: true }).isVisible()); await page.keyboard.press('Escape'); }
        const help = page.locator('.admin-help-trigger').first(); await help.focus(); await page.getByRole('tooltip').waitFor(); await page.keyboard.press('Escape'); await page.getByRole('tooltip').waitFor({ state: 'detached' });
        await page.screenshot({ path: path.join(out, `system-${width}.png`) });
        evidence.push({ route: '/admin/system-base', width, saveVisible: true, helpFocusEscape: true });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await visit('/admin/model-hub');
    await page.screenshot({ path: path.join(out, 'models.png') });
    await page.getByRole('button', { name: '调整', exact: true }).first().click();
    await page.getByRole('dialog').waitFor();
    assert.ok((await page.getByRole('dialog').innerText()).includes('fixture-provider::sample-chat'));
    await page.keyboard.press('Escape');
    await visit('/admin/chat-runtime?tab=subagents');
    assert.ok((await page.locator('h1').allTextContents()).some(text => text.includes('代理')));
    await page.screenshot({ path: path.join(out, 'agents.png') });
    await visit('/admin/memory?tab=graph');
    await page.getByRole('button', { name: /Workspace A.*12/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    await page.getByRole('region', { name: '节点管理' }).waitFor();
    await page.getByRole('button', { name: '新建连接', exact: true }).click();
    await page.getByLabel('目标实体', { exact: true }).fill('new-target');
    failWrites = true;
    await page.getByRole('button', { name: '新建连接', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'fixture_failure' }).waitFor();
    assert.equal(await page.getByLabel('目标实体', { exact: true }).inputValue(), 'new-target');
    assert.equal(writes.at(-1).body.workspaceKey, 'a'); assert.equal(writes.at(-1).body.subject, 'shared');
    failWrites = false;
    await page.getByRole('button', { name: '新建连接', exact: true }).click();
    await page.getByRole('button', { name: '断开关系', exact: true }).first().waitFor();
    await page.getByRole('button', { name: '断开关系', exact: true }).first().click();
    await page.waitForTimeout(200);
    assert.equal(writes.at(-1).body.scope, 'workspace:a');
    await page.getByRole('button', { name: '返回全景', exact: true }).click();
    await page.getByRole('button', { name: /全局记忆.*12/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    assert.equal(await page.getByRole('region', { name: '节点管理' }).getByRole('button', { name: '新建连接', exact: true }).count(), 0);
    await page.screenshot({ path: path.join(out, 'galaxy.png') });
    evidence.push({ route: '/admin/memory?tab=graph', globalCount: sampleClusters.filter(item => item.clusterId === 'global').length, writeFailureRetainsTarget: true, capturedWorkspace: 'a', globalReadonly: true });
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(out, 'interaction-evidence.json'), JSON.stringify({ evidence, writes, errors }, null, 2));
    console.log(JSON.stringify({ ok: true, evidence, writes: writes.length, output: out }));
} catch (error) { await page.screenshot({ path: path.join(out, 'failure.png') }); console.error(JSON.stringify({ error: String(error), pageErrors: errors, body: (await page.locator('body').innerText()).slice(-5000) })); process.exitCode = 1; }
finally { await browser.close(); }
