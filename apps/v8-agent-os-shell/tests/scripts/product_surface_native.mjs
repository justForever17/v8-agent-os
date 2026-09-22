// Explicit live acceptance against the production Engine, Product Web and Shell.
// Creates only synthetic records in the supplied isolated state; no model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { verifyResidentRoundTrips } from '../../../../scripts/experience/9131/shell-resident-scenario.mjs';

const args = process.argv.slice(2);
const option = name => args.includes(name) ? args[args.indexOf(name) + 1] : '';
assert.ok(args.includes('--live'), 'Explicit --live and isolated --state-root/--out required');
const repo = path.resolve(import.meta.dirname, '../../../..');
const state = path.resolve(option('--state-root'));
const out = path.resolve(option('--out'));
assert.ok(option('--state-root') && option('--out'));
assert.notEqual(state, path.parse(state).root);
assert.ok(fs.existsSync(path.join(state, 'config.json')), 'Start an isolated production Preview first');
assert.ok(state.includes(`${path.sep}out${path.sep}`) || state.includes(`${path.sep}tmp${path.sep}`), 'Synthetic state must be under out/ or tmp/');
fs.mkdirSync(out, { recursive: true });
const env = { ...process.env, V8_REPO_ROOT: repo, V8_AGENT_OS_HOME: state,
  V8OS_DESKTOP_ISOLATED_USER_DATA_ROOT: path.join(state, 'electron') };
delete env.ELECTRON_RUN_AS_NODE;
const cli = (command, extra = []) => JSON.parse(execFileSync(process.execPath,
  [path.join(repo, 'apps/v8-agent-os-cli/bin/v8os.mjs'), command, ...extra, '--json'],
  { cwd: repo, env, encoding: 'utf8', timeout: 60000, windowsHide: true }));
const ports = JSON.parse(fs.readFileSync(path.join(state, 'runtime/cli/ports.json'), 'utf8')).ports;
assert.equal(ports.admin, ports.web, 'One Product Web listener is required');
const origin = `http://127.0.0.1:${ports.web}`;
const require = createRequire(path.join(repo, 'apps/v8-agent-os-web/package.json'));
const { _electron } = require('playwright');
const errors = [];
const report = { level: 'REAL_ELECTRON_PRODUCTION_ENGINE_WEB', ports,
  qualification: 'Synthetic workspace/session and actual UI actions; no provider/model completion or installer claim.', errors };
let app;
try {
  report.before = cli('status').map(({ id, state, managed }) => ({ id, state, managed }));
  cli('stop', ['--only', 'shell']);
  app = await _electron.launch({
    executablePath: path.join(repo, 'apps/v8-agent-os-shell/node_modules/electron/dist', process.platform === 'win32' ? 'electron.exe' : 'electron'),
    args: [path.join(repo, 'apps/v8-agent-os-shell')], cwd: repo, env, timeout: 120000,
  });
  app.context().on('page', page => page.on('pageerror', error => errors.push({ path: new URL(page.url()).pathname, message: error.message.slice(0, 400) })));
  async function waitSurface(kind) {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      const page = app.context().pages().find(page => {
        try { const url = new URL(page.url()); return url.origin === origin && (kind === 'web' ? url.pathname.startsWith('/chat') : /^\/(admin|login)(\/|$)/.test(url.pathname)); }
        catch { return false; }
      });
      if (page) { page.setDefaultTimeout(15000); return page; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw Error(`Surface unavailable: ${kind}`);
  }
  const web = await waitSurface('web');
  await web.getByRole('button', { name: /^(控制台|Console|Admin)$/ }).click();
  const admin = await waitSurface('admin');
  await admin.locator('#login').waitFor();
  // This fixture uses a new managed local Owner. Password never leaves memory.
  const login = `surface-${crypto.randomUUID().slice(0, 8)}`;
  const password = crypto.randomUUID();
  assert.ok(await admin.locator('#name').count(), 'Use a fresh isolated state; existing credentials are never replaced');
  await admin.locator('#login').fill(login);
  await admin.locator('#name').fill('Synthetic surface acceptance');
  await admin.locator('#password').fill(password);
  await admin.locator('#confirmPassword').fill(password);
  await admin.locator('button[type=submit]').click();
  await admin.waitForURL('**/admin', { timeout: 60000 });
  const sidebar = await admin.locator('.admin-app > div > aside').evaluate(el => ({ display: getComputedStyle(el).display, width: el.getBoundingClientRect().width }));
  assert.equal(sidebar.display, 'flex', 'Production CSS must include migrated Admin responsive classes');
  assert.ok(sidebar.width >= 200, 'Expanded navigation must retain its layout width');
  assert.equal(await admin.evaluate(async () => (await (await fetch('/api/auth/session')).json()).user?.adminAuthenticated), true);
  report.ownerConfiguredThroughUi = true;
  await admin.getByRole('button', { name: '语言切换', exact: true }).click();
  await admin.getByRole('menuitem', { name: '切换到英文', exact: true }).click();
  await admin.waitForFunction(() => document.documentElement.lang === 'en');
  await web.waitForFunction(() => document.documentElement.lang === 'en');
  await admin.getByRole('button', { name: 'Language switcher', exact: true }).click();
  await admin.getByRole('menuitem', { name: 'Switch to Chinese', exact: true }).click();
  await web.waitForFunction(() => document.documentElement.lang === 'zh-CN');
  report.languageSynchronizedAcrossResidentDocuments = true;
  await admin.getByRole('button', { name: /^(聊天|Chat)$/ }).click();
  const workspace = path.join(state, 'synthetic-workspace');
  fs.mkdirSync(workspace, { recursive: true });
  const created = await web.evaluate(async workspacePath => {
    const projectResponse = await fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Surface acceptance', workspacePath, workspaceTrustState: 'trusted' }) });
    const project = await projectResponse.json();
    const response = await fetch('/api/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Surface draft acceptance', projectId: project.id || project.project?.id, workspacePath }) });
    return { projectStatus: projectResponse.status, sessionStatus: response.status, session: await response.json() };
  }, workspace);
  assert.ok(created.projectStatus >= 200 && created.projectStatus < 300);
  assert.equal(created.sessionStatus, 200);
  const sessionId = created.session.id || created.session.sessionId;
  assert.ok(sessionId);
  await web.goto(`${origin}/chat?id=${encodeURIComponent(sessionId)}`);
  const composer = web.locator('textarea[data-v8os-chat-composer="true"]');
  await composer.waitFor();
  await composer.fill('同一 Web 宿主切换后的草稿保持 intact 🌙');
  await composer.press('Home');
  await composer.press('Shift+ArrowRight');
  const observe = page => page.evaluate(() => {
    window.__experienceDoc ||= crypto.randomUUID();
    window.v8osShell.onSurfaceVisibilityChange(state => { window.__surfaceVisible = state.visible; });
  });
  await observe(web); await observe(admin);
  const baseline = await composer.evaluate(e => ({ text: e.value, start: e.selectionStart, end: e.selectionEnd,
    scroll: document.querySelector('.v8-chat-viewport-surface').scrollTop, doc: window.__experienceDoc, timeOrigin: performance.timeOrigin }));
  const adminBaseline = await admin.evaluate(() => ({ doc: window.__experienceDoc, timeOrigin: performance.timeOrigin }));
  report.residency = await verifyResidentRoundTrips({ web, admin, app, out, baseline, adminBaseline }, 30);
  await web.getByRole('button', { name: /^(控制台|Console|Admin)$/ }).click();
  await admin.goto(`${origin}/admin/desktop-pet`);
  await admin.locator('#companion-skin').click();
  await admin.getByRole('option', { name: /柔光团子|Soft Orb/ }).click();
  const save = admin.getByRole('button', { name: /^(保存|保存配置|保存更改|Save|Save changes|Save configuration)$/ });
  const saved = admin.waitForResponse(response => response.url().includes('/api/admin/config-registry/desktop-pet') && response.request().method() === 'POST');
  await save.click();
  assert.equal((await saved).status(), 200);
  await admin.reload();
  await admin.locator('#companion-skin').waitFor();
  assert.match(await admin.locator('#companion-skin').innerText(), /柔光团子|Soft Orb/);
  await admin.getByRole('switch', { name: /桌宠|companion|Desktop Pet/i }).first().click();
  const switchStyle = await admin.getByRole('switch', { name: /桌宠|companion|Desktop Pet/i }).first().evaluate(el => ({
    width: el.getBoundingClientRect().width, background: getComputedStyle(el).backgroundColor }));
  assert.ok(switchStyle.width >= 40);
  assert.notEqual(switchStyle.background, 'rgba(0, 0, 0, 0)', 'Checked switch must have its state background');
  const companionDeadline = Date.now() + 30000;
  let companion;
  while (!companion && Date.now() < companionDeadline) {
    companion = app.context().pages().find(page => page.url().startsWith('v8-desktop:'));
    if (!companion) await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(companion, 'Shell must own the companion window');
  await companion.locator('[data-companion-skin="soft-orb"] img').waitFor();
  assert.equal(await companion.locator('[data-companion-skin="soft-orb"] img').evaluate(image => image.complete && image.naturalWidth > 0), true);
  const hostPid = await app.evaluate(() => process.pid);
  const descriptor = JSON.parse(fs.readFileSync(path.join(state, 'runtime/companion-window.json'), 'utf8'));
  assert.equal(descriptor.pid, hostPid);
  assert.equal(descriptor.runtimeKind, 'companion-window');
  report.skin = { savedAndReloaded: true, imageRendered: true, sameHostPid: true };
  await admin.screenshot({ path: path.join(out, 'admin-companion.png') });
  await companion.screenshot({ path: path.join(out, 'companion.png') });
  await admin.getByRole('switch', { name: /桌宠|companion|Desktop Pet/i }).first().click();
  await companion.waitForEvent('close', { timeout: 15000 }).catch(() => assert.ok(companion.isClosed()));
  report.companionClosedWithoutShellExit = await app.evaluate(() => process.pid) === hostPid;
  assert.ok(report.companionClosedWithoutShellExit);
  await admin.getByRole('button', { name: /退出登录|Sign out/i }).click();
  await admin.waitForURL('**/login');
  const authAfterSignout = await admin.evaluate(async () => (await (await fetch('/api/auth/session')).json()).user?.adminAuthenticated === true);
  assert.equal(authAfterSignout, false);
  const lockedApi = await admin.evaluate(async () => (await fetch('/api/admin/config-registry/desktop-pet')).status);
  assert.ok([401, 403].includes(lockedApi), 'A residual local chat session must not access configuration');
  report.adminSignedOutAndAccessRevoked = true;
  assert.deepEqual(errors, []);
  report.ok = true;
} catch (error) {
  report.ok = false;
  report.error = String(error.stack || error).slice(0, 2400);
  if (app) for (const [index, page] of app.context().pages().entries()) {
    try { await page.screenshot({ path: path.join(out, `failure-${index}.png`) }); } catch {}
  }
  process.exitCode = 1;
} finally {
  try { if (app) await app.close(); } catch {}
  try { report.cleanup = cli('stop').map(({ id, status, reason }) => ({ id, status, reason })); }
  catch { report.cleanupFailed = true; process.exitCode = 1; }
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, error: report.error, cleanupFailed: report.cleanupFailed, report: path.join(out, 'result.json') }));
}
