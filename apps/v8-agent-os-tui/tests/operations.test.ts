import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { Surface } from '../src/surface.js';
import { editor } from '../src/terminal.js';
import { editExternal, editorArgv } from '../src/external-editor.js';
import { packCommands } from '../src/feature-packs.js';
import { previewPlugin, pluginJob } from '../src/extension-pages.js';
import { createPeerInvitation, consumePeerInvitation } from '../src/peer-pages.js';

function make(t: any, api: any = async () => ({})) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-tui-operations-'));
  const client = new Client(new ViewStore(root, 'fixture'), api);
  client.instance = { instanceId: 'fixture' }; client.view.sessionId = 's';
  client.sessions = [{ id: 's' }]; client.syncCursor = 'cursor';
  t.after(() => { client.stop(); rmSync(root, { recursive: true, force: true }); });
  return { client, ui: new Surface(client) };
}

test('palette exposes query, match count and empty result; empty Enter is inert', async t => {
  let writes = 0; const { ui, client } = make(t, async (_route: string, opts: any) => { if (opts?.method) writes++; return {}; });
  client.setDraft('保留草稿'); ui.palette();
  await ui.handle({ key: 'text', text: '编辑器' });
  assert.match(ui.page!.lines.join('\n'), /搜索：编辑器/); assert.match(ui.page!.lines.join('\n'), /匹配 1 \//);
  await ui.handle({ key: 'text', text: 'xyz' }); assert.equal(ui.page!.actions.length, 0);
  assert.match(ui.page!.lines.join('\n'), /没有匹配/); await ui.handle({ key: 'down' }); await ui.handle({ key: 'enter' });
  assert.equal(ui.page!.selected, 0); assert.equal(writes, 0); assert.equal(client.draft.text, '保留草稿');
});

test('slow reads permit local navigation and detach; late pages cannot replace current page', async t => {
  let release: (value: any) => void = () => {}, posts = 0, exited = 0;
  const { ui } = make(t, async (_route: string, opts: any) => {
    if (opts?.method) posts++;
    return new Promise(resolve => { release = resolve; });
  });
  ui.onExit = () => { exited++; };
  const loading = ui.dispatch({ key: 'ctrl-t' });
  assert.equal(ui.busy, true);
  await ui.dispatch({ key: 'escape' }); assert.equal(ui.page, null);
  await ui.dispatch({ key: 'ctrl-p' }); await ui.dispatch({ key: 'text', text: '帮助' });
  assert.match(ui.page!.lines.join(''), /搜索：帮助/);
  await ui.dispatch({ key: 'enter' }); assert.equal(ui.page!.title, '帮助 / 首次安装');
  release({ currentRun: { id: 'r', status: 'completed' } }); await loading;
  assert.equal(ui.page!.title, '帮助 / 首次安装');
  const pending = ui.dispatch({ key: 'ctrl-t' }); await ui.dispatch({ key: 'ctrl-d' }); assert.equal(exited, 1);
  release({}); await pending; assert.equal(posts, 0);
});

test('pending writes permit detach and browsing but no second mutation', async t => {
  const { ui, client } = make(t); let release: () => void = () => {}, writes = 0, exited = 0;
  const work = ui.execute(() => new Promise<void>(resolve => { release = resolve; writes++; }));
  ui.onExit = () => { exited++; };
  await ui.dispatch({ key: 'ctrl-p' }); await ui.dispatch({ key: 'text', text: '发送' });
  client.setDraft('preserved'); await ui.dispatch({ key: 'enter' }); assert.equal(writes, 1);
  await ui.dispatch({ key: 'pagedown' }); await ui.dispatch({ key: 'ctrl-d' }); assert.equal(exited, 1);
  assert.equal(client.draft.text, 'preserved'); release(); await work;
});

test('empty numeric budgets stay on the form and send no request; explicit zero means unlimited', async t => {
  let writes = 0;
  const { ui } = make(t, async (_route: string, opts: any) => {
    if (opts?.method) { writes++; assert.equal(opts.body.governance.budgets.globalDailyTokenLimit, 0); return { transactionId: 'p', planDigest: 'd', state: 'ready_to_commit' }; }
    return { config: { governance: { budgets: {} } } };
  });
  await ui.budgets(); ui.formEditor = editor('  '); await ui.dispatch({ key: 'f9' });
  assert.equal(ui.page!.title, '累计预算'); assert.equal(writes, 0); assert.match(ui.client.notice, /不能为空/);
  ui.formEditor = editor('0'); await ui.dispatch({ key: 'f9' }); assert.equal(writes, 1); assert.equal(ui.page!.title, '配置变更预览');
});

test('attach uses actual session binding while new drafts retain selected workspace', async t => {
  const { client } = make(t, async route => route.endsWith('/scope') ? { binding: { workspace_path: '/bound/session' } } : { messages: [] });
  client.view.workspace = '/new/default'; client.notice = '新建对话'; await client.attach('s');
  assert.equal(client.workspace, '/bound/session'); assert.match(client.notice, /已恢复会话/);
  await client.attach(''); assert.equal(client.workspace, '/new/default'); assert.match(client.notice, /新建对话/);
});

test('retry refreshes capability and target; lost response is durable and never replays prose', async t => {
  let writes = 0, runId = 'r', canRetry = false;
  const { client, ui } = make(t, async (route: string, opts: any) => {
    if (opts?.method === 'POST') { writes++; assert.equal(route, '/v1/runs/r/commands/retry'); throw new Error('lost reply'); }
    return { currentRun: { id: runId, status: 'failed' }, controls: { runId, canRetry } };
  });
  client.setDraft('do not resend'); await client.refreshSnapshot(); ui.retryRun(); assert.match(ui.page!.lines.join(''), /未提供重试/);
  canRetry = true; await client.refreshSnapshot(); ui.retryRun(); assert.equal(ui.page!.selected, 0);
  runId = 'other'; await assert.rejects(ui.page!.actions[1].run(), /任务已变化/); assert.equal(writes, 0);
  runId = 'r'; await client.refreshSnapshot(); await client.retryRun('r'); assert.equal(writes, 1);
  const reopened = new Client(client.store, client.api); reopened.instance = client.instance;
  t.after(() => reopened.stop()); await assert.rejects(reopened.retryRun('r'), /待确认/);
  assert.equal(writes, 1); assert.equal(client.draft.text, 'do not resend');
});

test('external editor returns to unsent draft and fences changed draft/session/instance', async t => {
  const { client, ui } = make(t); client.setDraft('原文'); let removed = 0;
  ui.onEditor = async text => ({ text: text + '\n新文👩‍🚀', file: 'private-copy', remove: async () => { removed++; } });
  ui.palette(); await ui.externalEditor(); assert.equal(client.draft.text, '原文\n新文👩‍🚀');
  assert.equal(ui.page, null); assert.equal(ui.input.pasted, true); assert.equal(removed, 1);
  await ui.handle({ key: 'enter' }); assert.equal(client.draft.unknown, undefined);
  for (const drift of ['draft', 'session', 'instance']) {
    ui.onEditor = async () => {
      if (drift === 'draft') client.setDraft('external update');
      if (drift === 'session') client.view.sessionId = 'other';
      if (drift === 'instance') client.view = { ...client.view, instanceId: 'other-instance' };
      return { text: 'must not overwrite', file: 'preserved-copy', remove: async () => { removed++; } };
    };
    const originalView = client.view;
    await assert.rejects(ui.externalEditor(), /保留在 preserved-copy/);
    assert.notEqual(client.draft.text, 'must not overwrite'); assert.equal(removed, 1);
    client.view = originalView; client.view.sessionId = 's';
  }
});

test('editor argv is literal; a real child edits Unicode and failure preserves its private file', async t => {
  assert.deepEqual(editorArgv('"C:\\Program Files\\Editor\\edit.exe" --wait "two words"'), ['C:\\Program Files\\Editor\\edit.exe', '--wait', 'two words']);
  for (const command of ['nano; whoami', '$(whoami)', 'sh -c "nano"', 'nano "unterminated']) assert.throws(() => editorArgv(command));
  const directory = mkdtempSync(path.join(os.tmpdir(), 'v8-tui-editor-test-')); t.after(() => rmSync(directory, { recursive: true, force: true }));
  const script = path.join(directory, 'edit.mjs');
  writeFileSync(script, "import fs from 'node:fs'; fs.appendFileSync(process.argv.at(-1), '\\n改动👩‍🚀');");
  const command = `"${process.execPath}" "${script}"`;
  const result = await editExternal('原文', { command }); assert.equal(result.text, '原文\n改动👩‍🚀'); await result.remove(); assert.equal(existsSync(result.file), false);
  writeFileSync(script, 'process.exit(3);');
  await assert.rejects(editExternal('retain me', { command }), error => {
    const file = String(error).split('草稿副本保留在 ')[1]; assert.equal(readFileSync(file, 'utf8'), 'retain me');
    rmSync(path.dirname(file), { recursive: true, force: true }); return true;
  });
});

test('pack commands preserve exact state root and safely quote spaces/metacharacters', () => {
  const commands = packCommands({ bundleRoot: '/a', launcher: "/a b/it's/v8os", stateRoot: '/state;literal' }, 'browser');
  assert.match(commands.install, /^V8_AGENT_OS_HOME='\/state;literal'/); assert.ok(commands.install.includes("'/a b/it'\"'\"'s/v8os'"));
  assert.match(commands.preview, /--dry-run$/); assert.throws(() => packCommands({ bundleRoot: '', launcher: '', stateRoot: '' }, 'browser;id'));
});

test('plugin uses approved digest and idempotency key; uncertain response cannot be replayed', async t => {
  let writes = 0;
  const { ui } = make(t, async (_route: string, opts: any) => {
    if (opts?.body?.dryRun) return { dryRun: true, jobId: 'preview', planDigest: 'digest', plan: { installable: true } };
    writes++; assert.equal(opts.body.planDigest, 'digest'); assert.equal(opts.body.approved, true); assert.ok(opts.body.idempotencyKey); throw new Error('lost response');
  });
  await previewPlugin(ui, 'fixture'); assert.equal(ui.page!.actions[0].label, '返回'); assert.equal(writes, 0);
  const install = ui.page!.actions[1].run; await install(); assert.equal(writes, 1); assert.equal(ui.page!.title, '安装结果待确认');
  await assert.rejects(install(), /已经提交/); assert.equal(writes, 1);
});

test('plugin ready without a receipt cannot claim installation; rollback failure requires diagnosis', async t => {
  let job: any = { state: 'ready', result: { ok: true } };
  const { ui } = make(t, async () => job); await pluginJob(ui, 'j'); assert.match(ui.page!.lines[0], /尚未确认/);
  job = { state: 'ready', result: { ok: true, state: 'degraded', receipt: { manifestDigest: 'd' } } };
  await pluginJob(ui, 'j'); assert.match(ui.page!.lines[0], /degraded/);
  job = { state: 'rollback_failed', pluginId: 'fixture' }; await pluginJob(ui, 'j');
  assert.ok(ui.page!.actions.some(a => a.label === '检查插件当前状态')); assert.ok(!ui.page!.actions.some(a => a.label.includes('重新预览')));
});

test('peer invitation creation and consume are explicit, ephemeral and read back exact peer', async t => {
  const invitation = JSON.stringify({ kind: 'v8-peer-invitation.v1', peerId: 'remote', displayName: 'Remote', baseUrl: 'http://fixture:9530', publicKey: 'fixture-public-key', code: 'TEST-ONLY-CODE', expiresAt: 'future' });
  let consumes = 0, creates = 0;
  const { client, ui } = make(t, async (route: string, opts: any) => {
    if (route.endsWith('/pairing/invitations')) { creates++; return { ok: true, inviteId: 'invite', code: 'TEST-ONLY-CODE', invitation, expiresAt: 'future' }; }
    if (route.endsWith('/pairing/consume')) { consumes++; assert.equal(opts.body.invitation, invitation); return { ok: true }; }
    return { items: [{ peerId: 'remote', linkId: 'link' }] };
  });
  createPeerInvitation(ui); await ui.handle({ key: 'f9' }); assert.equal(creates, 1); assert.doesNotMatch(ui.page!.lines.join(''), /TEST-ONLY-CODE/);
  consumePeerInvitation(ui); ui.formEditor = editor(invitation); await ui.handle({ key: 'f9' });
  assert.equal(ui.page!.selected, 0); assert.doesNotMatch(ui.page!.lines.join(''), /TEST-ONLY-CODE/); assert.equal(consumes, 0);
  client.save(true); assert.doesNotMatch(readFileSync(client.store.file, 'utf8'), /TEST-ONLY-CODE/);
  await ui.page!.actions[1].run(); assert.equal(consumes, 1); assert.match(client.notice, /已回读到/);
  assert.equal(client.draft.text, '');
});
