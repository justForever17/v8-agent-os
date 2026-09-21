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
  assert.equal(ui.suggestions!.query.text, '帮助'); assert.equal(ui.page, null);
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

test('context settings expose usage/history and reject values Engine would silently normalize', async t => {
  const calls: string[] = [];
  const { ui, client } = make(t, async (route: string, opts: any) => {
    calls.push(route);
    if (route === '/v1/config-registry/context') return { data: { policy: { compression: { enabled: true, mode: 'persistent_baseline', default_context_window_tokens: 32000, trigger_ratio: 0.94, keep_recent_turns: 4, keep_recent_messages: 8 } }, bindings: { summary_model: 'Engine 默认' } } };
    if (route.startsWith('/v1/observability/compactions')) return { items: [{ createdAt: 'now', trigger_reason: 'threshold', summary_tokens: 100, estimated_saved_tokens: 900, covered_message_count: 6 }] };
    if (route.startsWith('/v1/telemetry/overview')) return { stats: { recentWindowTokens: 1234 } };
    if (route.includes('/snapshot')) return { contextGovernance: { context_window_tokens: 32000, estimated_input_tokens: 1200, estimated_effective_input_tokens: 900, compaction_applied: false } };
    return {};
  });
  await ui.contextSettings();
  assert.equal(ui.page?.title, '上下文与压缩');
  assert.ok(ui.page?.actions.some(action => action.label === '查看当前上下文用量'));
  assert.ok(ui.page?.actions.some(action => action.label === '查看最近压缩记录'));
  ui.editContextBasics(client as any, { enabled: true, mode: 'persistent_baseline', default_context_window_tokens: 32000, trigger_ratio: 0.94, keep_recent_turns: 4, keep_recent_messages: 8 });
  assert.equal(ui.page?.title, '上下文压缩策略');
  ui.page!.fieldIndex = 2;
  ui.formEditor = editor('1024');
  await ui.dispatch({ key: 'f9' });
  assert.match(client.notice, /上下文窗口必须是 2048/);
  await ui.contextUsage();
  assert.match(ui.page?.lines.join('\n') || '', /context_window_tokens/);
  await ui.compactionHistory();
  assert.match(ui.page?.lines.join('\n') || '', /节省约 900/);
  assert.ok(calls.some(route => route.startsWith('/v1/observability/compactions')));
});

test('reasoning effort is session scoped and only advertises Engine supplied levels', async t => {
  const { ui, client } = make(t, async (route: string, opts: any) => {
    if (route.startsWith('/v1/models/supervisor-reasoning-effort') && opts?.method === 'PATCH') return { effectiveLevel: 'high', selectionSource: 'session' };
    if (route.startsWith('/v1/models/supervisor-reasoning-effort')) return { effectiveLevel: 'auto', selectionSource: 'model_default', levels: ['auto', 'high'] };
    return {};
  });
  await ui.reasoningEffort();
  assert.ok(ui.page?.actions.some(action => action.label === '设为 high'));
  assert.ok(!ui.page?.actions.some(action => action.label === '设为 medium'));
  await ui.page?.actions.find(action => action.label === '设为 high')?.run();
  assert.match(ui.page?.lines.join('\n') || '', /high/);
});

test('model settings remain navigable when optional control-plane inventory is unavailable', async t => {
  const { ui } = make(t, async (route: string) => {
    if (route === '/v1/config-broker/roles') return { roles: [{ role: 'supervisor', status: 'ready' }] };
    if (route === '/v1/models/control-plane') throw new Error('control plane warming up');
    return {};
  });
  await ui.models();
  assert.equal(ui.page?.title, '模型 / Provider / 预算');
  assert.match(ui.page?.lines.join('\n') || '', /模型目录暂无可用条目/);
});

test('attach uses actual session binding while new drafts retain selected workspace', async t => {
  const { client } = make(t, async route => route.endsWith('/scope') ? { binding: { workspace_path: '/bound/session' } } : { messages: [] });
  client.view.workspace = '/new/default'; client.notice = '新建对话'; await client.attach('s');
  assert.equal(client.workspace, '/bound/session'); assert.match(client.notice, /已恢复会话/);
  await client.attach(''); assert.equal(client.workspace, '/new/default'); assert.match(client.notice, /新建对话/);
});

test('submit preserves structured @ session and skill references in the Engine request', async t => {
  let submitted: any;
  const { client } = make(t, async (route: string, opts: any) => {
    if (route === '/v1/chat/submit') { submitted = opts.body; return { accepted: true, runId: 'run-at' }; }
    if (route.includes('/timeline/sync')) return { messages: [], syncCursor: 'cursor' };
    if (route.includes('/runtime-events')) return { events: [] };
    if (route.includes('/snapshot')) return { currentRun: { id: 'run-at', status: 'completed' } };
    return {};
  });
  client.setDraft('请参考 @session:session-1 @skill:browser');
  await client.submit({ contextSessionRefs: [{ sessionId: 'session-1', source: 'history_menu' }], contextMentions: [{ kind: 'skill', name: 'browser', label: 'browser', sourceType: 'explicit_mention' }] });
  assert.deepEqual(submitted.data.contextSessionRefs, [{ sessionId: 'session-1', source: 'history_menu' }]);
  assert.equal(submitted.data.contextMentions[0].name, 'browser');
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
  const started = path.join(directory, 'child.json');
  writeFileSync(script, `import fs from 'node:fs'; fs.writeFileSync(${JSON.stringify(started)}, JSON.stringify({ pid: process.pid, file: process.argv.at(-1) })); setInterval(() => {}, 1000);`);
  const controller = new AbortController();
  const pending = editExternal('cancelled draft', { command, signal: controller.signal });
  const failed = assert.rejects(pending, /草稿副本保留在/);
  const deadline = Date.now() + 5000;
  while (!existsSync(started) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(existsSync(started)); const child = JSON.parse(readFileSync(started, 'utf8'));
  controller.abort(); await failed;
  assert.throws(() => process.kill(child.pid, 0), /ESRCH/);
  assert.equal(readFileSync(child.file, 'utf8'), 'cancelled draft'); rmSync(path.dirname(child.file), { recursive: true, force: true });
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
