import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import os from 'node:os';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { ViewStore } from '../src/persistence.js';
import { localize } from '../src/locale.js';

function createTestClient(tmpDir: string, apiRoutes: Record<string, any> = {}) {
  const store = new ViewStore(tmpDir);
  const client = new Client(store, async (route: string, options?: any) => {
    if (route.endsWith('/instance')) return { instanceId: 'test-inst' };
    if (route.endsWith('/owner')) return { initialized: true, user: { sessionIdentifier: 'test-user@v8.dev' } };
    if (route.includes('/models/readiness')) return { ready: true };
    if (apiRoutes[route]) {
      return typeof apiRoutes[route] === 'function' ? apiRoutes[route](options) : apiRoutes[route];
    }
    return {};
  });
  client.instance = { instanceId: 'test-inst' };
  client.view.sessionId = 'test-session';
  client.view.workspace = tmpDir;
  client.sessionWorkspace = tmpDir;
  return client;
}

test('commands list includes diff, rewind, restore and init as daily tier', () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-cmd-test-'));
  const client = createTestClient(tmpDir);
  const surface = new Surface(client);
  const cmds = surface.commands();

  const diffCmd = cmds.find(c => c.command === 'diff');
  assert.ok(diffCmd, 'diff command must exist');
  assert.equal(diffCmd.tier, 'daily');

  const rewindCmd = cmds.find(c => c.command === 'rewind');
  assert.ok(rewindCmd, 'rewind command must exist');
  assert.equal(rewindCmd.tier, 'daily');

  const restoreCmd = cmds.find(c => c.command === 'restore');
  assert.ok(restoreCmd, 'restore command must exist');
  assert.equal(restoreCmd.tier, 'daily');

  const initCmd = cmds.find(c => c.command === 'init');
  assert.ok(initCmd, 'init command must exist');
  assert.equal(initCmd.tier, 'daily');
});

test('getWorkspaceDiff handles clean and non-git directories gracefully', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-diff-clean-'));
  const client = createTestClient(tmpDir);
  const res = await client.getWorkspaceDiff();
  assert.ok(typeof res.clean === 'boolean');
  assert.ok(Array.isArray(res.files));
  assert.ok(Array.isArray(res.untrackedFiles));
});

test('rewindLastTurn truncates turn and restores user input to draft', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-rewind-test-'));
  let patchedPayload: any = null;

  const client = createTestClient(tmpDir);
  client.messages = [
    { id: 'msg-1', role: 'user', content: '编写一个计算斐波那契数列的函数' },
    { id: 'msg-2', role: 'assistant', content: '好的，这是代码实现...' },
  ];

  client.api = async (route: string, options?: any) => {
    if (route.includes('/messages/msg-1') && options?.method === 'PATCH') {
      patchedPayload = options.body;
      return { success: true };
    }
    if (route.includes('/turns')) return { messages: [] };
    if (route.includes('/scope')) return { binding: { workspacePath: tmpDir } };
    if (route.includes('/snapshot')) return { latestSeq: 1 };
    return {};
  };

  const res = await client.rewindLastTurn();
  assert.equal(res.success, true);
  assert.equal(res.text, '编写一个计算斐波那契数列的函数');
  assert.equal(client.draft.text, '编写一个计算斐波那契数列的函数');
  assert.ok(patchedPayload, 'PATCH must be called on Engine');
  assert.equal(patchedPayload.tailPolicy, 'truncate');
});

test('rewindLastTurn throws cleanly if no user messages exist', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-rewind-empty-'));
  const client = createTestClient(tmpDir);
  client.messages = [];

  await assert.rejects(async () => {
    await client.rewindLastTurn();
  }, /暂无用户消息可回滚/);
});

test('initProjectContract detects Node.js / TypeScript stack and generates AGENTS.md with backup', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-init-test-'));
  writeFileSync(path.join(tmpDir, 'package.json'), JSON.stringify({
    name: 'test-app',
    dependencies: { react: '^19.0.0', next: '^15.0.0', typescript: '^5.0.0' },
    scripts: { test: 'vitest run', build: 'next build' },
  }, null, 2));

  const client = createTestClient(tmpDir);
  const result = await client.initProjectContract();

  assert.equal(result.created, true);
  const agentsPath = path.join(tmpDir, 'AGENTS.md');
  assert.ok(existsSync(agentsPath), 'AGENTS.md must be generated');

  const content = readFileSync(agentsPath, 'utf-8');
  assert.ok(content.includes('test-app'));
  assert.ok(content.includes('Next.js'));
  assert.ok(content.includes('Supervisor First, Runtime Grounded'));

  // Second init backs up existing file
  const secondResult = await client.initProjectContract();
  assert.equal(secondResult.created, false);
  assert.ok(existsSync(path.join(tmpDir, 'AGENTS.md.bak')), 'AGENTS.md.bak backup must be created');
});

test('locale translates diff, rewind, restore and init correctly', () => {
  assert.equal(localize('工作区修改 /diff', 'en-US'), 'Workspace diff /diff');
  assert.equal(localize('回滚轮次 /rewind', 'en-US'), 'Rewind turn /rewind');
  assert.equal(localize('恢复版本 /restore', 'en-US'), 'Restore revision /restore');
  assert.equal(localize('初始化规范 /init', 'en-US'), 'Initialize contract /init');
  assert.equal(localize('工作区修改 /diff', 'zh-CN'), '工作区修改 /diff');
});

test('surface.submit intercepts /diff, /rewind, /restore and /init', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-surface-sub-'));
  const client = createTestClient(tmpDir);
  const surface = new Surface(client);

  let diffCalled = false;
  surface.diffAction = async () => { diffCalled = true; };
  client.setDraft('/diff');
  surface.input = { text: '/diff', cursor: 5 };
  await surface.submit();
  assert.equal(diffCalled, true);
  assert.equal(surface.input.text, '');

  let rewindCalled = false;
  surface.rewindAction = async () => { rewindCalled = true; };
  client.setDraft('/rewind');
  surface.input = { text: '/rewind', cursor: 7 };
  await surface.submit();
  assert.equal(rewindCalled, true);
  assert.equal(surface.input.text, '');

  let restoreCalled = false;
  surface.restoreAction = async () => { restoreCalled = true; };
  client.setDraft('/restore');
  surface.input = { text: '/restore', cursor: 8 };
  await surface.submit();
  assert.equal(restoreCalled, true);
  assert.equal(surface.input.text, '');

  let initCalled = false;
  surface.initAction = async () => { initCalled = true; };
  client.setDraft('/init');
  surface.input = { text: '/init', cursor: 5 };
  await surface.submit();
  assert.equal(initCalled, true);
  assert.equal(surface.input.text, '');
});


test('rewindLastTurn handles multimodal structured content cleanly', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-rewind-multimodal-'));
  const client = createTestClient(tmpDir);
  client.messages = [
    {
      id: 'msg-complex-1',
      role: 'user',
      content: [
        { type: 'text', text: '请分析这一段错误日志并给出修复方案：' },
        { type: 'text', text: 'TypeError: Cannot read property undefined' },
      ],
    },
    { id: 'msg-complex-2', role: 'assistant', content: '修复方案如下...' },
  ];

  client.api = async (route: string, options?: any) => {
    if (route.includes('/messages/msg-complex-1') && options?.method === 'PATCH') {
      return { success: true };
    }
    if (route.includes('/turns')) return { messages: [] };
    if (route.includes('/scope')) return { binding: { workspacePath: tmpDir } };
    if (route.includes('/snapshot')) return { latestSeq: 1 };
    return {};
  };

  const res = await client.rewindLastTurn();
  assert.equal(res.success, true);
  assert.equal(res.text, '请分析这一段错误日志并给出修复方案：TypeError: Cannot read property undefined');
  assert.equal(client.draft.text, '请分析这一段错误日志并给出修复方案：TypeError: Cannot read property undefined');
});

test('initProjectContract handles hybrid polyglot workspace (Rust + Node)', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-polyglot-'));
  writeFileSync(path.join(tmpDir, 'package.json'), JSON.stringify({ name: 'polyglot-app' }));
  writeFileSync(path.join(tmpDir, 'Cargo.toml'), '[package]\nname = "polyglot-core"\nversion = "0.1.0"');

  const client = createTestClient(tmpDir);
  const result = await client.initProjectContract();
  assert.equal(result.created, true);

  const content = readFileSync(path.join(tmpDir, 'AGENTS.md'), 'utf-8');
  assert.ok(content.includes('Node.js'));
  assert.ok(content.includes('Rust / Cargo'));
});

test('diffAction displays notice cleanly when workspace is non-git or error', async () => {
  const tmpDir = mkdtempSync(path.join(os.tmpdir(), 'v8-diff-err-'));
  const client = createTestClient(tmpDir);
  const surface = new Surface(client);

  client.getWorkspaceDiff = async () => ({
    clean: true,
    files: [],
    totalAdditions: 0,
    totalDeletions: 0,
    untrackedFiles: [],
    error: 'fatal: not a git repository',
  });

  await surface.diffAction();
  assert.ok(surface.page, 'Diff page must open');
  assert.ok(surface.page.title.includes('Diff'));
  assert.ok(surface.page.lines.some(l => l.includes('fatal: not a git repository')));
});

