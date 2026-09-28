import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { mkdtempSync } from 'node:fs';
import os from 'node:os';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { ViewStore, defaultView } from '../src/persistence.js';
import { detectSystemLanguage, normalizeLocale, localize } from '../src/locale.js';
import { visibleCommandMatches } from '../src/command-suggestions.js';

function createFixtureClient() {
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-locale-test-')));
  const client = new Client(store, async (route: string) => {
    if (route.endsWith('/instance')) return { instanceId: 'test-inst' };
    if (route.endsWith('/owner')) return { initialized: true, user: { sessionIdentifier: 'test-owner' } };
    if (route.includes('/models/readiness')) return { ready: true };
    if (route.includes('/branches')) return { sessionId: 'new-branch-sess', title: '分支会话' };
    if (route.includes('/config-registry/context')) return { data: { policy: { compression: { default_context_window_tokens: 32000, soft_trigger_ratio: 0.9, keep_recent_turns: 4 } } } };
    return {};
  });
  client.instance = { instanceId: 'test-inst' };
  client.view.sessionId = 'test-session';
  return client;
}

test('detectSystemLanguage detects environment and platform locale accurately', () => {
  const originalV8Lang = process.env.V8OS_LANG;
  const originalLang = process.env.LANG;
  const originalLcAll = process.env.LC_ALL;
  try {
    delete process.env.V8OS_LANG;
    delete process.env.LANG;
    delete process.env.LC_ALL;

    // 1. Explicit V8OS_LANG takes highest precedence
    process.env.V8OS_LANG = 'en-US';
    assert.equal(detectSystemLanguage(), 'en-US');
    process.env.V8OS_LANG = 'zh-CN';
    assert.equal(detectSystemLanguage(), 'zh-CN');

    // 2. POSIX LANG works when V8OS_LANG is not set
    delete process.env.V8OS_LANG;
    process.env.LANG = 'en_US.UTF-8';
    assert.equal(detectSystemLanguage(), 'en-US');
    process.env.LANG = 'zh_TW.UTF-8';
    assert.equal(detectSystemLanguage(), 'zh-CN');

    // 3. Fallback to Intl when env is absent (simulate Windows)
    delete process.env.LANG;
    const systemLocale = detectSystemLanguage();
    assert.ok(['zh-CN', 'en-US'].includes(systemLocale));
  } finally {
    if (originalV8Lang !== undefined) process.env.V8OS_LANG = originalV8Lang;
    else delete process.env.V8OS_LANG;
    if (originalLang !== undefined) process.env.LANG = originalLang;
    else delete process.env.LANG;
    if (originalLcAll !== undefined) process.env.LC_ALL = originalLcAll;
    else delete process.env.LC_ALL;
  }
});

test('defaultView initializes with detectSystemLanguage', () => {
  const view = defaultView();
  assert.ok(view.locale === 'zh-CN' || view.locale === 'en-US');
});

test('localize converts Chinese labels and prefixes to English when locale is en-US', () => {
  assert.equal(localize('语言 / Language', 'en-US'), 'Language / 语言');
  assert.equal(localize('模型管理 /model', 'en-US'), 'Model hub /model');
  assert.equal(localize('语言已切换为：English', 'en-US'), 'Language switched to: English');
  assert.equal(localize('语言 / Language', 'zh-CN'), '语言 / Language');
});

test('visibleCommandMatches exposes /language and /lang in root slash menu', () => {
  const client = createFixtureClient();
  const surface = new Surface(client);
  const commands = surface.commands();

  const langCmd = commands.find(c => c.command === 'language');
  assert.ok(langCmd, 'language command must exist');
  assert.equal(langCmd.tier, 'daily', 'language command must have daily tier so it shows up in root /');

  const matches = visibleCommandMatches(commands, '');
  const commandsInMenu = matches.map(m => m.command);
  assert.ok(commandsInMenu.includes('language'), 'Expected /language in empty / command suggestions');
  assert.ok(commandsInMenu.includes('lang'), 'Expected /lang in empty / command suggestions');

  client.stop();
});

test('/language command switches locale directly or opens language dialog', async () => {
  const client = createFixtureClient();
  const surface = new Surface(client);

  // 1. /language without args opens dialog
  client.setDraft('/language');
  await surface.submit();
  assert.ok(surface.page, 'Expected dialog to open');
  assert.equal(surface.page.title, '语言 / Language');

  // 2. /language en switches locale to en-US
  client.setDraft('/language en');
  await surface.submit();
  assert.equal(client.view.locale, 'en-US');
  assert.equal(client.draft.text, '');
  assert.match(client.notice, /English/);

  // 3. /language zh switches locale back to zh-CN
  client.setDraft('/language zh');
  await surface.submit();
  assert.equal(client.view.locale, 'zh-CN');
  assert.equal(client.draft.text, '');
  assert.match(client.notice, /简体中文/);

  // 4. Unknown slash command is intercepted and not sent to model
  let submitCalled = false;
  client.submit = async () => { submitCalled = true; };
  client.setDraft('/unknown_foo_bar');
  await surface.submit();
  assert.equal(submitCalled, false, 'Unknown command should not be submitted to model');
  assert.match(client.notice, /未知命令/);

  client.stop();
});

test('/fork and /branch commands exist in daily tier and switch to branched session', async () => {
  const client = createFixtureClient();
  const surface = new Surface(client);
  const commands = surface.commands();

  const forkCmd = commands.find(c => c.command === 'fork');
  const branchCmd = commands.find(c => c.command === 'branch');
  assert.ok(forkCmd, 'Expected /fork command to be registered');
  assert.ok(branchCmd, 'Expected /branch command to be registered');
  assert.equal(forkCmd.tier, 'daily');
  assert.equal(branchCmd.tier, 'daily');

  // Test /fork without args opens form
  client.setDraft('/fork');
  await surface.submit();
  assert.ok(surface.page, 'Expected fork form to open');
  assert.equal(surface.page.title, '分叉会话分支 / Fork');

  // Test /branch with args directly forks
  client.setDraft('/branch experimental-run');
  await surface.submit();
  assert.equal(client.view.sessionId, 'new-branch-sess');
  assert.match(client.notice, /已分叉为新分支会话/);

  client.stop();
});

test('/compress command shows current context usage and compression policy', async () => {
  const client = createFixtureClient();
  const surface = new Surface(client);

  client.setDraft('/compress');
  await surface.submit();
  assert.ok(surface.page, 'Expected compress dialog to open');
  assert.equal(surface.page.title, '上下文用量与压缩 / Compress');
  assert.ok(surface.page.lines.some(l => l.includes('窗口上限：32000')));

  client.stop();
});
