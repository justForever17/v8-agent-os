// Independent UI oracles. Reuse only the author's frozen public network fixture shape.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { installGraphicsProbe } from './graph-observation.mjs';

const repo = path.resolve(import.meta.dirname, '../../..');
const args = process.argv.slice(2);
const value = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const candidate = value('--candidate', 'ccbcd59017759fd0ad8f5a43e3c8dd73860250f5');
const runtime = value('--runtime', 'dev-turbopack');
assert.ok(['dev-turbopack', 'production'].includes(runtime), 'runtime must match the explicitly handed-off process');
const selectedTests = value('--only', '').split(',').filter(Boolean);
const resultName = selectedTests.length ? 'focused-' + selectedTests.join('-').replaceAll(/[^a-zA-Z0-9_-]/g, '') + '.json' : 'independent-evidence.json';
const base = 'http://127.0.0.1:22828';
const out = path.join(repo, 'tmp/experience-9131/admin-candidate-' + candidate.slice(0, 8));
fs.mkdirSync(out, { recursive: true });
const git = (...args) => execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' }).trim();
assert.equal(git('rev-parse', candidate), candidate);
const fixtureFile = path.join(out, 'frozen-author-network-fixture.mjs');
fs.writeFileSync(fixtureFile, git('show', candidate + ':apps/v8-agent-os-admin/scripts/admin-experience-fixture.mjs'));
const { adminExperienceFixture, sampleClusters } = await import(pathToFileURL(fixtureFile).href);
const require = createRequire(path.join(repo, 'apps/v8-agent-os-admin/package.json'));
const { chromium } = require('playwright');
const browserExecutable = value('--browser-executable', process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined);
const browser = await chromium.launch({ headless: true, executablePath: browserExecutable });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN', reducedMotion: 'reduce' });
await context.addInitScript(installGraphicsProbe);
const page = await context.newPage();
page.setDefaultTimeout(12000);
const evidence = [], requests = [], writes = [], pageErrors = [], assetHashes = new Map();
const clone = value => structuredClone(value);
let failWrite = false, failConfigReads = false, theme = 'light', holdGraphWrite = false, releaseGraphWrite;
const initialConfig = () => clone(adminExperienceFixture(base + '/api/config-registry/system-base'));
let config = initialConfig();
let graph = clone(sampleClusters);
const report = { candidateCommit: candidate, fixtureCommit: candidate, authorDeclaredRuntime: runtime, sourceCheckedThrough: 'local Git object database in experience checkout plus explicit process handoff', browserVersion: browser.version(), viewport: { width: 1440, height: 900, dpr: 1 }, evidence, requests, writes, pageErrors };

function graphRead(url) {
  const key = url.searchParams.get('clusterId');
  const cluster = graph.find(item => item.clusterId === key);
  if (url.searchParams.has('entity')) {
    const entity = url.searchParams.get('entity');
    const relations = (cluster?.links || []).filter(edge => edge.source === entity || edge.target === entity).map(edge => ({ ...edge, subject: edge.source, object: edge.target, predicate: edge.label }));
    return { relations, total: relations.length, nextOffset: null };
  }
  return { items: cluster ? [cluster] : graph, totalWorkspaces: 2, nextOffset: null, partial: false };
}
async function routeBoundary(route) {
  const req = route.request(), url = new URL(req.url());
  if (url.origin !== base) { requests.push({ path: url.pathname, method: req.method(), result: 'external-aborted' }); return route.abort(); }
  if (!url.pathname.startsWith('/api/')) return route.continue();
  if (['/api/auth/providers','/api/auth/csrf','/api/auth/callback/credentials','/api/auth/session'].includes(url.pathname)) return route.continue();
  const record = { path: url.pathname, method: req.method() }; requests.push(record);
  if (req.method() !== 'GET') {
    const body = req.postDataJSON(); writes.push({ ...record, body });
    if (url.pathname === '/api/ui-preferences/theme') { theme = body.theme; return route.fulfill({ json: { theme } }); }
    if (failWrite) { record.result = 'synthetic-503'; return route.fulfill({ status: 503, json: { error: 'independent_fixture_write_failed' } }); }
    if (url.pathname === '/api/config-registry/system-base') { config = { ...config, data: clone(body.data) }; return route.fulfill({ json: config }); }
    if (url.pathname === '/api/memory/graph') {
      if (holdGraphWrite) await new Promise(resolve => { releaseGraphWrite = resolve; });
      const cluster = graph.find(x => x.workspaceKey === body.workspaceKey && x.scopeKind === 'workspace');
      if (!cluster) return route.fulfill({ status: 403, json: { error: 'fixture_scope_denied' } });
      if (body.action === 'add_relation') {
        if (!cluster.nodes.some(x => x.id === body.subject)) cluster.nodes.push({ id: body.subject, label: body.subject, type: 'concept' });
        if (!cluster.nodes.some(x => x.id === body.object)) cluster.nodes.push({ id: body.object, label: body.object, type: 'concept' });
        cluster.links.push({ relationId: 'independent-created-' + writes.length, source: body.subject, target: body.object, label: body.predicate, scope: 'workspace:' + body.workspaceKey, confidence: 1, version: 'fixture-mutated' });
      } else if (body.action === 'delete_relation') cluster.links = cluster.links.filter(x => !(x.source === body.subject && x.target === body.object && x.label === body.predicate && x.scope === body.scope));
      else if (body.action === 'delete_entity') { cluster.links = cluster.links.filter(x => x.source !== body.name && x.target !== body.name); cluster.nodes = cluster.nodes.filter(x => x.id !== body.name); }
      cluster.meta.totalRelations = cluster.meta.renderedRelations = cluster.links.length;
      cluster.meta.totalEntities = cluster.meta.renderedEntities = cluster.nodes.length;
      return route.fulfill({ json: { created: body.action === 'add_relation', deleted: body.action !== 'add_relation' } });
    }
    record.result = 'unlisted-write-blocked';
    return route.fulfill({ status: 409, json: { error: 'independent_fixture_write_not_declared' } });
  }
  let fixture = adminExperienceFixture(req.url());
  if (url.pathname === '/api/config-registry/system-base') fixture = config;
  if (url.pathname.startsWith('/api/config-registry/') && failConfigReads) fixture = undefined;
  if (url.pathname === '/api/ui-preferences/theme') fixture = { theme };
  if (url.pathname === '/api/memory/graph') fixture = graphRead(url);
  record.result = fixture === undefined ? 'synthetic-503' : 'synthetic-200';
  return route.fulfill(fixture === undefined ? { status: 503, json: { error: 'independent_fixture_unavailable' } } : { json: fixture });
}
await context.route('**/*', routeBoundary);
page.on('pageerror', error => pageErrors.push({ route: new URL(page.url()).pathname, error: error.message }));
const pendingHashes = [];
page.on('response', response => {
  const url = new URL(response.url());
  if (url.origin === base && url.pathname.startsWith('/_next/static/') && /\.js$/.test(url.pathname)) {
    pendingHashes.push(response.body().then(body => assetHashes.set(url.pathname, { bytes: body.length, sha256: crypto.createHash('sha256').update(body).digest('hex') })).catch(() => {}));
  }
});
const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const visit = async route => { await page.goto(base + route, { waitUntil: 'networkidle', timeout: 60000 }); await settle(); };
const rectInViewport = async locator => {
  const box = await locator.boundingBox();
  const viewport = page.viewportSize();
  return { box, viewport, inside: Boolean(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1) };
};
async function test(id, dimension, fn) {
  if (selectedTests.length && !selectedTests.some(name => id.startsWith(name))) return;
  const row = { id, dimension, layer: 'real browser / synthetic API boundary' };
  try { row.details = await fn(); row.status = row.details?.observationOnly ? 'OBSERVED' : 'PASS'; }
  catch (error) { row.status = error.name === 'AssertionError' ? 'FAIL' : 'HARNESS_ERROR'; row.error = String(error).slice(0, 1600); }
  row.actualUrl = page.url(); row.screenshot = id + '.png';
  await page.screenshot({ path: path.join(out, row.screenshot) }).catch(() => {});
  evidence.push(row); console.log(JSON.stringify({ id, status: row.status, details: row.details, error: row.error }));
  fs.writeFileSync(path.join(out, resultName), JSON.stringify(report, null, 2));
}

try {
  await visit('/login');
  await page.locator('#login').fill('admin-experience-fixture');
  // Deliberately public synthetic account supplied for this isolated state only.
  await page.locator('#password').fill('public-admin-experience-fixture');
  await page.locator('button[type=submit]').click();
  await page.waitForURL('**/admin', { timeout: 60000 });

  await test('A01-save-bar-bounds', 'convenience', async () => {
    await visit('/admin/system-base');
    const measurements = [];
    for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size); await settle();
      const save = page.locator('#admin-save-actions button').first(); await save.waitFor();
      const measurement = await rectInViewport(save); measurements.push(measurement);
      assert.equal(measurement.inside, true, 'save action must be visible before scrolling');
    }
    return measurements;
  });
  await test('A05-touch-first-tap-help', 'convenience', async () => {
    const touch = await browser.newContext({ storageState: await context.storageState(), viewport: { width: 390, height: 844 }, locale: 'zh-CN', isMobile: true, hasTouch: true, reducedMotion: 'reduce' });
    await touch.route('**/*', routeBoundary);
    const tp = await touch.newPage();
    try {
      await tp.goto(base + '/admin/system-base', { waitUntil: 'networkidle' });
      const button = tp.locator('.admin-help-trigger').filter({ visible: true }).first();
      await button.tap();
      const state = await button.getAttribute('aria-expanded');
      const visible = await tp.getByRole('tooltip').count();
      await tp.screenshot({ path: path.join(out, 'touch-help.png') });
      assert.equal(state, 'true', 'a first touch must open help; focus + click must not toggle it shut');
      assert.equal(visible, 1);
      return { touchEmulation: true, ariaExpanded: state, tooltipCount: visible };
    } finally { await touch.close(); }
  });
  await test('A05-keyboard-help-escape', 'convenience', async () => {
    await page.setViewportSize({ width: 1440, height: 900 }); await visit('/admin/system-base');
    const help = page.locator('.admin-help-trigger').filter({ visible: true }).first();
    await help.focus(); await page.getByRole('tooltip').waitFor();
    const bounds = await rectInViewport(page.getByRole('tooltip'));
    await page.keyboard.press('Escape'); await page.getByRole('tooltip').waitFor({ state: 'detached' });
    assert.ok(bounds.inside); return { bounds, removedOnEscape: true };
  });
  await test('A06-narrow-navigation', 'convenience', async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await visit('/admin/system-base');
    await page.getByRole('button', { name: '导航', exact: true }).click();
    const dialog = page.getByRole('dialog'); await dialog.waitFor();
    await dialog.getByRole('link', { name: '模型', exact: true }).click();
    await page.waitForURL('**/admin/model-hub');
    await page.getByText('Fixture provider', { exact: true }).first().waitFor();
    return { reachedModelHub: true, remainingDialogs: await page.getByRole('dialog').count() };
  });
  await test('A02-config-roundtrip-unknowns', 'function', async () => {
    await page.setViewportSize({ width: 1440, height: 900 }); config = initialConfig();
    await visit('/admin/system-base');
    await page.locator('summary').filter({ hasText: /^服务联通$/ }).click();
    const field = page.getByPlaceholder('ws://127.0.0.1:9530/v1', { exact: true });
    await field.fill('ws://127.0.0.1:7/v1');
    const before = writes.length;
    await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === '/api/config-registry/system-base' && response.request().method() === 'POST'),
      page.locator('#admin-save-actions button').first().click(),
    ]);
    assert.equal(writes.length, before + 1);
    assert.deepEqual(config.data.customOpaqueFixture, initialConfig().data.customOpaqueFixture);
    assert.deepEqual(config.data.bridge.fixtureUnknown, initialConfig().data.bridge.fixtureUnknown);
    assert.equal(config.data.bridge.internalSecret, '***');
    await page.reload({ waitUntil: 'networkidle' }); await page.locator('summary').filter({ hasText: /^服务联通$/ }).click();
    assert.equal(await field.inputValue(), 'ws://127.0.0.1:7/v1');
    return { boundaryPersistedAndReloaded: true, unknownsRetained: true, placeholderRetained: true, actualEngineWrite: false };
  });
  await test('A03-config-failure-retains-draft', 'function', async () => {
    await visit('/admin/system-base'); await page.locator('summary').filter({ hasText: /^服务联通$/ }).click();
    const persistedBefore = clone(config);
    const field = page.getByPlaceholder('ws://127.0.0.1:9530/v1', { exact: true });
    await field.fill('ws://127.0.0.1:8/v1'); failWrite = true;
    try {
      await page.locator('#admin-save-actions button').first().click();
      const error = page.getByRole('alert').filter({ hasText: 'independent_fixture_write_failed' }); await error.waitFor();
      assert.equal(await field.inputValue(), 'ws://127.0.0.1:8/v1');
      const bounds = await rectInViewport(error); assert.ok(bounds.inside);
      assert.deepEqual(config, persistedBefore);
      return { draftRetained: true, errorReachable: true, savedConfigUnchanged: true };
    } finally { failWrite = false; }
  });
  await test('A03-model-dirty-escape', 'function', async () => {
    await visit('/admin/model-hub');
    await page.getByRole('button', { name: '调整', exact: true }).first().click();
    await page.getByRole('dialog').waitFor();
    if (!await page.locator('#model-model-id').count()) await page.getByRole('dialog').getByTitle('编辑', { exact: true }).click();
    const dialog = page.locator('[role=dialog]').filter({ has: page.locator('#model-model-id') }); await dialog.waitFor();
    await dialog.locator('#model-model-id').fill('unsaved-independent-model');
    let prompted = false;
    const listener = async prompt => { prompted = true; await prompt.dismiss(); };
    page.on('dialog', listener);
    await page.keyboard.press('Escape'); await settle();
    page.off('dialog', listener);
    if (!await dialog.count()) {
      if (await page.getByRole('dialog').count()) await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '调整', exact: true }).first().click(); await page.getByRole('dialog').waitFor();
      if (!await page.locator('#model-model-id').count()) await page.getByRole('dialog').getByTitle('编辑', { exact: true }).click(); await dialog.waitFor();
    }
    const actual = await dialog.locator('#model-model-id').inputValue();
    const acceptDiscard = prompt => prompt.accept(); page.once('dialog', acceptDiscard);
    await page.keyboard.press('Escape');
    page.off('dialog', acceptDiscard);
    assert.equal(actual, 'unsaved-independent-model', 'Escape without explicit discard must retain the model draft or keep the editor open');
    return { prompted, value: actual };
  });
  await test('A08-agent-query-and-back', 'function', async () => {
    await visit('/admin/chat-runtime?tab=subagents');
    await page.getByText('Fixture specialist', { exact: true }).waitFor();
    const tabs = await page.getByRole('tab').allTextContents();
    return { route: new URL(page.url()).pathname + new URL(page.url()).search, fixtureAgentVisible: true, tabs };
  });
  await test('G03-write-failure-scope-and-readback', 'function', async () => {
    graph = clone(sampleClusters); await visit('/admin/memory?tab=graph');
    await page.getByRole('button', { name: /Workspace A.*12/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    const menu = page.getByRole('region', { name: '节点管理', exact: true }); await menu.waitFor();
    await menu.getByRole('button', { name: '新建连接', exact: true }).click();
    await page.getByLabel('目标实体', { exact: true }).fill('independent-new-node');
    failWrite = true;
    await menu.getByRole('button', { name: '新建连接', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'independent_fixture_write_failed' }).waitFor();
    assert.equal(await page.getByLabel('目标实体', { exact: true }).inputValue(), 'independent-new-node');
    assert.equal(writes.at(-1).body.workspaceKey, 'a');
    const bBefore = clone(graph.find(x => x.workspaceKey === 'b'));
    failWrite = false;
    await menu.getByRole('button', { name: '新建连接', exact: true }).click();
    await menu.getByText('shared → RELATED_TO → independent-new-node', { exact: false }).waitFor();
    assert.deepEqual(graph.find(x => x.workspaceKey === 'b'), bBefore);
    const bounds = await rectInViewport(menu); assert.ok(bounds.inside, JSON.stringify(bounds));
    return { actualApi: 'synthetic stateful boundary', failureRetainsDraft: true, targetScope: 'workspace:a', newRelationReadbackVisible: true, otherClusterUnchanged: true, bounds };
  });
  await test('G03-global-readonly', 'function', async () => {
    await visit('/admin/memory?tab=graph');
    await page.getByRole('button', { name: /全局记忆.*12/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    const menu = page.getByRole('region', { name: '节点管理', exact: true }); await menu.waitFor();
    assert.equal(await menu.getByRole('button', { name: '新建连接', exact: true }).count(), 0);
    return { writesUnavailableInUI: true, backendPermissionVerified: false };
  });
  await test('G03-node-menu-escape', 'convenience', async () => {
    await visit('/admin/memory?tab=graph');
    await page.getByRole('button', { name: /Workspace A/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    const menu = page.getByRole('region', { name: '节点管理', exact: true }); await menu.waitFor();
    await menu.getByRole('button', { name: '管理全部关系', exact: true }).focus();
    await page.keyboard.press('Escape'); await settle();
    assert.equal(await menu.count(), 0, 'clean node menu should close with Escape while focus is in the menu');
    const focus = await page.evaluate(() => ({ tag: document.activeElement?.tagName, label: document.activeElement?.getAttribute('aria-label') || (document.activeElement?.tagName==='BODY' ? '' : document.activeElement?.textContent?.slice(0,80)), connected: document.activeElement?.isConnected }));
    return { closedWithKeyboard: true, focusAfterClose: focus };
  });
  await test('G03-pending-edit-retention', 'function', async () => {
    graph = clone(sampleClusters); await visit('/admin/memory?tab=graph');
    await page.getByRole('button', { name: /Workspace A/ }).click();
    await page.getByRole('button', { name: 'shared', exact: true }).click();
    const menu = page.getByRole('region', { name: '节点管理', exact: true }); await menu.waitFor();
    await menu.getByRole('button', { name: '新建连接', exact: true }).click();
    const field = page.getByLabel('目标实体', { exact: true });
    await field.fill('submitted-v1'); holdGraphWrite = true;
    try {
      await menu.getByRole('button', { name: '新建连接', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('#galaxy-target') && [...document.querySelectorAll('button')].some(b => b.disabled && b.textContent.includes('新建连接')));
      const editable = await field.isEditable();
      if (editable) await field.fill('unsent-v2');
      const deadline = Date.now() + 5000;
      while (!releaseGraphWrite && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(releaseGraphWrite, 'synthetic request reached the controlled deferred boundary');
      releaseGraphWrite(); releaseGraphWrite = undefined; holdGraphWrite = false;
      await menu.getByText('shared → RELATED_TO → submitted-v1', { exact: false }).waitFor();
      if (!await field.count()) await menu.getByRole('button', { name: '新建连接', exact: true }).click();
      const retained = await field.inputValue();
      assert.ok(!editable || retained === 'unsent-v2', `pending field editable=${editable}; after v1 response value=${JSON.stringify(retained)}`);
      return { fieldLockedWhilePending: !editable, newDraftRetained: retained === 'unsent-v2' };
    } finally { holdGraphWrite = false; releaseGraphWrite?.(); releaseGraphWrite = undefined; }
  });
  await test('A07-error-retry-system-base', 'function', async () => {
    failConfigReads = true;
    try {
      await visit('/admin/system-base');
      const error = page.locator('.admin-page [role=alert]'); await error.waitFor();
      const title = await page.locator('h1').innerText();
      const spinnerCount = await page.locator('.admin-page .animate-spin').count();
      assert.equal(spinnerCount, 0);
      failConfigReads = false;
      await error.getByRole('button', { name: '重试', exact: true }).click();
      await page.locator('#admin-save-actions button').first().waitFor();
      return { title, failedReadStopsSpinner: true, retryRestoresEditablePage: true };
    } finally { failConfigReads = false; }
  });
  await test('V01-layout-theme-and-text-zoom', 'visual', async () => {
    const observations = [];
    for (const variant of [{ width: 1440, theme: 'light', scale: 1 }, { width: 390, theme: 'light', scale: 1 }, { width: 1440, theme: 'dark', scale: 1 }, { width: 1440, theme: 'light', scale: 2 }]) {
      theme = variant.theme; await page.setViewportSize({ width: variant.width, height: 900 });
      await visit('/admin/model-hub'); await page.getByText('Fixture provider', { exact: true }).first().waitFor();
      if (variant.scale === 2) await page.evaluate(() => { document.documentElement.style.fontSize = '28px'; });
      await settle();
      const computed = await page.evaluate(() => {
        const app = document.querySelector('.admin-app'), style = getComputedStyle(app);
        const card = document.querySelector('.group\\/card');
        const box = el => { const r = el?.getBoundingClientRect(); return r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null; };
        return { theme: document.documentElement.className, rootFont: getComputedStyle(document.documentElement).fontSize, page: box(document.querySelector('.admin-page')), provider: box(card), providerRadius: card && getComputedStyle(card).borderRadius, buttons: [...document.querySelectorAll('button')].filter(b => ['调整','管理'].includes(b.textContent.trim()) || b.getAttribute('aria-label') === '管理').map(b => ({ label: b.textContent.trim() || b.getAttribute('aria-label'), ...box(b), radius: getComputedStyle(b).borderRadius })), topbarHeightToken: style.getPropertyValue('--v8-product-topbar-height'), motion: ['--admin-motion-fast','--admin-motion-control','--admin-motion-panel'].map(k => style.getPropertyValue(k)), overflow: document.documentElement.scrollWidth > innerWidth + 1 };
      });
      observations.push({ variant, computed });
      await page.screenshot({ path: path.join(out, `visual-${variant.theme}-${variant.width}-${variant.scale}.png`) });
      assert.equal(computed.overflow, false, JSON.stringify({ variant, computed }));
      assert.equal(computed.topbarHeightToken.trim(), '48px');
    }
    await page.setViewportSize({ width: 1440, height: 900 }); theme = 'light';
    return { observations, textZoomQualification: 'root-font enlargement; fixed-pixel text does not scale, so not full browser 200% zoom acceptance' };
  });
  await test('V02-shared-radius-consumption', 'visual', async () => {
    theme = 'light'; await page.setViewportSize({ width: 1440, height: 900 });
    await visit('/admin/model-hub'); await page.getByText('Fixture provider', { exact: true }).first().waitFor();
    const actual = await page.evaluate(() => ({
      providerCard: getComputedStyle(document.querySelector('.group\\/card')).borderRadius,
      manageButton: getComputedStyle(document.querySelector('button[aria-label="管理"]')).borderRadius,
      adjustButton: getComputedStyle([...document.querySelectorAll('button')].find(b => b.textContent.trim() === '调整')).borderRadius,
    }));
    assert.deepEqual(actual, { providerCard: '12px', manageButton: '8px', adjustButton: '8px' }, 'Admin common panel/control values must be consumed by actual cards and buttons');
    return actual;
  });
  await test('G05-global-only-to-workspace', 'function', async () => {
    graph = clone(sampleClusters); await page.setViewportSize({ width: 1440, height: 900 });
    await visit('/admin/memory?tab=graph');
    await page.getByRole('button', { name: /全局记忆.*12/ }).click();
    await page.getByRole('button', { name: 'global node 1', exact: true }).click();
    const menu = page.getByRole('region', { name: '节点管理', exact: true }); await menu.waitFor();
    const globalBefore = clone(graph[0]); const aBefore = clone(graph[1]);
    assert.equal(await menu.getByRole('button', { name: '删除', exact: true }).count(), 0);
    await menu.getByRole('button', { name: '在工作区新建连接', exact: true }).click();
    await page.getByLabel('目标实体', { exact: true }).fill('global-only-destination');
    const submit = menu.getByRole('button', { name: '新建连接', exact: true });
    assert.equal(await submit.isDisabled(), true, 'explicit target must be required before writing from global');
    await page.getByLabel('选择写入工作区', { exact: true }).selectOption('b');
    await submit.click();
    await menu.getByText('global-node-1 → RELATED_TO → global-only-destination', { exact: false }).waitFor();
    assert.equal(writes.at(-1).body.workspaceKey, 'b');
    assert.equal(writes.at(-1).body.subject, 'global-node-1');
    assert.deepEqual(graph[0], globalBefore); assert.deepEqual(graph[1], aBefore);
    assert.equal(graph[2].links.at(-1).scope, 'workspace:b');
    return { globalOnlySubject: true, explicitTarget: 'b', globalUnchanged: true, unrelatedAUnchanged: true, targetReadbackVisible: true, actualEngine: false };
  });
  await test('G04-canvas-motion-pause-hover', 'interaction-performance', async () => {
    graph = clone(sampleClusters); theme = 'light';
    await page.setViewportSize({ width: 1440, height: 900 }); await page.emulateMedia({ reducedMotion: 'no-preference' });
    await visit('/admin/memory?tab=graph');
    const canvas = page.locator('canvas[role=img]'); await canvas.waitFor(); await canvas.scrollIntoViewIfNeeded();
    await page.mouse.move(0, 0);
    await page.waitForFunction(() => window.__experienceGraphics().canvases[0]?.circles.filter(c => c.r > 10).length === 3);
    const probe = () => page.evaluate(() => window.__experienceGraphics());
    const before = await probe();
    await page.waitForTimeout(1800);
    const moving = await probe();
    const circles = moving.canvases[0].circles.filter(c => c.r > 10);
    assert.ok(moving.frames > before.frames + 10, 'visible unpaused graph continuously paints');
    const original = before.canvases[0].circles.filter(c => c.r > 10);
    assert.ok(Math.hypot(circles[1].x-original[1].x, circles[1].y-original[1].y) > 0.5, 'workspace orbital position changes');
    assert.ok(Math.hypot(circles[0].x-original[0].x, circles[0].y-original[0].y) < 0.1, 'global center stays fixed');
    const minimumGap = Math.min(...circles.flatMap((a,i) => circles.slice(i+1).map(b => Math.hypot(a.x-b.x,a.y-b.y)-a.r-b.r)));
    assert.ok(minimumGap > 0, 'painted cluster circles do not overlap');
    await page.getByRole('button', { name: '暂停运动', exact: true }).click(); await settle();
    const paused = await probe(); await page.waitForTimeout(600); const pausedAfter = await probe();
    assert.equal(pausedAfter.frames, paused.frames, 'pause leaves no continuous Canvas redraw');
    await page.getByRole('button', { name: '继续运动', exact: true }).click(); await page.mouse.move(0,0);
    await page.waitForTimeout(350);
    const hoverStart = await probe(), area = hoverStart.canvases[0], target = area.circles.filter(c=>c.r>10)[1];
    await page.mouse.move(area.rect.x+target.x, area.rect.y+target.y);
    await page.waitForTimeout(850); const hovered = await probe();
    await page.waitForTimeout(350); const hoveredAfter = await probe();
    assert.equal(hoveredAfter.frames, hovered.frames, 'hover settles and stops drawing');
    const hoverCircle = hovered.canvases[0].circles.filter(c=>c.r>10)[1];
    assert.ok(hoverCircle.r > target.r * 1.2, 'hover pulls the cluster nearer');
    await page.mouse.move(0,0); await page.waitForTimeout(800); const left = await probe();
    assert.ok(left.frames > hoveredAfter.frames + 5, 'pointer departure resumes motion');
    const samples = moving.drawCallbackMs.slice().sort((a,b)=>a-b);
    return { movingFrames: moving.frames-before.frames, globalFixed: true, minimumCircleGap: minimumGap, pausedFrames: pausedAfter.frames-paused.frames, hoverScale: hoverCircle.r/target.r, resumed: true, observedDrawSamples: samples.length, drawCallbackMedianMs: samples[Math.floor(samples.length/2)], drawCallbackP95Ms: samples[Math.ceil(samples.length*.95)-1], performanceQualification: 'Instrumented production Canvas callback timings including observer wrapper overhead, one stable segment; not end-to-end FPS or baseline comparison' };
  });
  await test('G04-reduced-visibility-unmount', 'interaction-performance', async () => {
    await page.emulateMedia({ reducedMotion: 'reduce' }); await visit('/admin/memory?tab=graph');
    await page.locator('canvas[role=img]').waitFor(); await page.locator('canvas[role=img]').scrollIntoViewIfNeeded(); await settle();
    const probe = () => page.evaluate(() => window.__experienceGraphics());
    const reduced = await probe(); await page.waitForTimeout(500); const reducedAfter = await probe();
    assert.equal(reducedAfter.frames, reduced.frames, 'reduced motion stops ongoing draws');
    await page.emulateMedia({ reducedMotion: 'no-preference' }); await page.mouse.move(0,0); await page.waitForTimeout(250);
    await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable:true, get:()=>true }); document.dispatchEvent(new Event('visibilitychange')); });
    const hidden = await probe(); await page.waitForTimeout(500); const hiddenAfter = await probe();
    assert.equal(hiddenAfter.frames, hidden.frames, 'hidden signal stops draw loop');
    await page.evaluate(() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForTimeout(400); const restored = await probe(); assert.ok(restored.frames > hiddenAfter.frames);
    const cycles=[];
    for(let index=0; index<20; index++) {
      await page.locator('a[href="/admin/memory?tab=preferences"]').click();
      await page.locator('canvas[role=img]').waitFor({state:'detached'}); await settle();
      const away = await probe();
      const requestCount=requests.filter(r=>r.path==='/api/memory/graph').length;
      if(index===0 || index===19) { await page.waitForTimeout(300); assert.equal((await probe()).frames,away.frames); assert.equal(requests.filter(r=>r.path==='/api/memory/graph').length,requestCount); }
      cycles.push({index, canvases:away.canvases.length, raf:away.raf, activeObservers:away.activeObservers});
      if(index<19) { await page.locator('a[href="/admin/memory?tab=graph"]').click(); await page.locator('canvas[role=img]').waitFor(); await settle(); }
    }
    assert.ok(cycles.every(row=>row.canvases===0));
    assert.ok(cycles.at(-1).raf <= cycles[0].raf + 1, 'no RAF count growth across twenty exits');
    for(const name of Object.keys(cycles[0].activeObservers)) assert.ok(cycles.at(-1).activeObservers[name] <= cycles[0].activeObservers[name]+1, `observer growth: ${name}`);
    await page.emulateMedia({ reducedMotion:'reduce' });
    return { reducedContinuousFrames:0, hiddenSignalFrames:0, visibilityQualification:'Synthetic document.hidden/visibilitychange injection. Headless Chrome tab switching stayed visible; no physical background claim.', restored:true, cycles, runtimeTaskExecution:'NOT_RUN: synthetic UI boundary only' };
  });
  await test('G03-keyboard-narrow-dark-menu', 'convenience-visual', async () => {
    const variants=[];
    for(const variant of [{width:1440,height:900,theme:'dark'},{width:390,height:844,theme:'light'},{width:1024,height:600,theme:'light'}]) {
      theme=variant.theme; await page.setViewportSize({width:variant.width,height:variant.height}); graph=clone(sampleClusters);
      await visit('/admin/memory?tab=graph');
      const cluster=page.getByRole('button',{name:/Workspace A.*12/}); await cluster.focus(); await page.keyboard.press('Enter');
      const node=page.getByRole('button',{name:'shared',exact:true}); await node.focus(); await page.keyboard.press('Enter');
      const menu=page.getByRole('region',{name:'节点管理',exact:true}); await menu.waitFor();
      await menu.getByRole('button',{name:'管理全部关系',exact:true}).click();
      const box=await rectInViewport(menu); assert.ok(box.inside,JSON.stringify(box));
      const close=menu.getByRole('button',{name:'关闭节点菜单',exact:true});
      const focusInside=await menu.evaluate(el=>el.contains(document.activeElement));
      await page.screenshot({path:path.join(out,`graph-menu-${variant.width}-${variant.height}-${variant.theme}.png`)});
      await close.focus(); await page.keyboard.press('Escape'); await menu.waitFor({state:'detached'});
      const focus=await page.evaluate(()=>({tag:document.activeElement?.tagName,label:document.activeElement?.getAttribute('aria-label') || (document.activeElement?.tagName==='BODY' ? '' : document.activeElement?.textContent?.slice(0,80)),connected:document.activeElement?.isConnected}));
      variants.push({variant,box,focusInside,focusAfterEscape:focus});
    }
    return {variants, keyboardActivation:true, touchQualification:'Viewport changes here; touch help is a separate actual browser emulation test'};
  });
  await test('G03-menu-focus-return', 'convenience', async () => {
    await page.setViewportSize({width:1440,height:900}); await visit('/admin/memory?tab=graph');
    const cluster=page.getByRole('button',{name:/Workspace A/}); await cluster.focus(); await page.keyboard.press('Enter');
    const node=page.getByRole('button',{name:'shared',exact:true}); await node.focus(); await page.keyboard.press('Enter');
    const menu=page.getByRole('region',{name:'节点管理',exact:true}); await menu.waitFor();
    await menu.getByRole('button',{name:'关闭节点菜单',exact:true}).focus(); await page.keyboard.press('Escape'); await menu.waitFor({state:'detached'}); await settle();
    const focus=await page.evaluate(()=>({tag:document.activeElement?.tagName,label:document.activeElement?.getAttribute('aria-label') || (document.activeElement?.tagName==='BODY' ? '' : document.activeElement?.textContent?.slice(0,80))}));
    assert.ok(focus.tag==='BUTTON' || focus.tag==='CANVAS',JSON.stringify(focus));
    return {focus, durableKeyboardTarget:true};
  });
  await test('G02-background-camera-sequence', 'interaction', async () => {
    graph=clone(sampleClusters); theme='light'; await page.setViewportSize({width:1440,height:900});
    await page.emulateMedia({reducedMotion:'no-preference'}); await visit('/admin/memory?tab=graph');
    const canvas=page.locator('canvas[role=img]'); await canvas.waitFor(); await canvas.scrollIntoViewIfNeeded(); await page.mouse.move(0,0);
    await page.waitForFunction(()=>window.__experienceGraphics().canvases[0]?.circles.filter(c=>c.r>10).length===3);
    const first=await page.evaluate(()=>window.__experienceGraphics()), c=first.canvases[0], target=c.circles.filter(x=>x.r>10)[1];
    // Click via observed geometry; no internal React state or camera mutation.
    await page.mouse.click(c.rect.x+target.x,c.rect.y+target.y); await page.mouse.move(0,0); await page.waitForTimeout(650);
    const selected=await page.evaluate(()=>window.__experienceGraphics()), area=selected.canvases[0];
    const large=area.circles.filter(x=>x.r>10)[1].r;
    const start=await page.evaluate(()=>performance.now());
    await page.mouse.click(area.rect.x+area.rect.width-12,area.rect.y+12); await page.mouse.move(0,0); await page.waitForTimeout(650);
    const ended=await page.evaluate(()=>window.__experienceGraphics());
    const small=ended.canvases[0].circles.filter(x=>x.r>10)[1].r;
    const series=[...ended.recentFrames,ended.canvases[0]].filter(f=>f.at>=start).map(f=>({at:f.at-start,r:f.circles.filter(x=>x.r>10)[1]?.r})).filter(x=>Number.isFinite(x.r));
    const intermediate=series.filter(x=>x.r>small+1 && x.r<large-1);
    assert.ok(large>small*1.4,'selected cluster zooms and background returns to overview');
    assert.ok(intermediate.length>=3,JSON.stringify({large,small,intermediate:intermediate.length,series:series.slice(0,12)}));
    return {selectedRadius:large,overviewRadius:small,intermediateFrames:intermediate.length,series:series.slice(0,40)};
  });
  await test('S01-admin-route-observations', 'coverage-observation', async () => {
    await page.emulateMedia({reducedMotion:'reduce'}); theme='light'; await page.setViewportSize({width:1440,height:900});
    const paths=git('ls-tree','-r','--name-only',candidate,'apps/v8-agent-os-admin/src/app/admin/(dashboard)').split('\n').filter(p=>p.endsWith('/page.tsx'));
    const routes=paths.map(p=>'/admin/'+p.split('/(dashboard)/')[1].replace(/\/page\.tsx$/,'').replace(/^page\.tsx$/,'')).map(p=>p.replace(/\/$/,'').replace('[id]','fixture-provider')).filter(p=>!p.startsWith('/admin/extensions'));
    routes.push(...['context','preferences','logs','knowledge','workflows','artifacts','graph','agent','runtime','config','upload'].map(tab=>'/admin/memory?tab='+tab));
    const observations=[];
    for(const [index, route] of routes.entries()) {
      const before=requests.length, errorBefore=pageErrors.length;
      try {
        await visit(route); await page.waitForTimeout(250);
        const actual=await page.evaluate(()=>{
          const visible=el=>el.getClientRects().length>0 && getComputedStyle(el).visibility!=='hidden';
          const content=document.querySelector('.admin-content');
          return {title:[...document.querySelectorAll('h1')].filter(visible).map(x=>x.innerText),alerts:[...document.querySelectorAll('.admin-content [role=alert]')].filter(visible).map(x=>x.innerText.slice(0,160)),spinners:content?.querySelectorAll('.animate-spin').length||0,layoutControls:[...content?.querySelectorAll('button,input,textarea,select')||[]].filter(visible).length,overflow:document.documentElement.scrollWidth>innerWidth+1,canvasCount:document.querySelectorAll('canvas').length,raf:window.__experienceGraphics().raf,activeObservers:window.__experienceGraphics().activeObservers};
        });
        const file=`route-sweep-${String(index).padStart(2,'0')}.png`; await page.screenshot({path:path.join(out,file)});
        observations.push({route,actualUrl:page.url(),...actual,requests:requests.length-before,errors:pageErrors.slice(errorBefore),screenshot:file});
      } catch(error) { observations.push({route,harnessError:String(error).slice(0,500)}); }
    }
    return {observationOnly:true,qualification:'Synthetic configured/unavailable page snapshots. Not all controls, true operations or complete feature acceptance; settled state may still depend on fixture coverage.',observations};
  });
} finally {
  failWrite = false;
  await Promise.allSettled(pendingHashes);
  report.assetHashes = Object.fromEntries(assetHashes);
  report.summary = { passed: evidence.filter(x => x.status === 'PASS').length, failed: evidence.filter(x => x.status === 'FAIL').length, harnessErrors: evidence.filter(x => x.status === 'HARNESS_ERROR').length, observed:evidence.filter(x=>x.status==='OBSERVED').length };
  fs.writeFileSync(path.join(out, resultName), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
  console.log(JSON.stringify({ summary: report.summary, out }));
  process.exitCode = report.summary.failed || report.summary.harnessErrors ? 1 : 0;
}
