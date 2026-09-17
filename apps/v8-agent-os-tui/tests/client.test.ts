import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { Surface, describe, containsSecretField } from '../src/surface.js';

function make(api: any) {
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-tui-test-')), 'engine-fixture');
  const client = new Client(store, api); client.view.sessionId = 's'; client.syncCursor = '2026-09-17T00:00:00Z';
  client.instance = { instanceId: 'engine-fixture' };
  client.connection = '已连接';
  client.sessions = [{ id: 's', title: 'Fixture' }];
  return client;
}
test('lost submit response persists unknown intent and blocks repetition after restart', async () => {
  let writes = 0;
  const client = make(async (route: string) => { if (route === '/v1/chat/submit') { writes++; throw new Error('response lost'); } return {}; });
  client.setDraft('请执行一次'); await client.submit();
  assert.equal(writes, 1); assert.equal(client.draft.text, '请执行一次'); assert.ok(client.draft.unknown);
  const next = new Client(client.store, client.api); next.instance = client.instance; await assert.rejects(next.submit(), /待确认/); assert.equal(writes, 1);
  const key = next.draft.unknown!.clientMessageId;
  next.messages = [{ id: 'm', metadata: { clientMessageId: key }, content: '请执行一次' }]; next.reconcile();
  assert.equal(next.draft.text, ''); assert.equal(next.draft.unknown, undefined); client.stop(); next.stop();
});
test('duplicate simultaneous send has a single write and clears only after accepted run evidence', async () => {
  let writes = 0, release: (x: any) => void = () => {};
  const client = make(async (route: string) => route === '/v1/chat/submit' ? (writes++, new Promise(r => { release = r; })) : {});
  client.setDraft('你好'); const first = client.submit(); const second = client.submit();
  await new Promise(r => setImmediate(r)); assert.equal(writes, 1); assert.equal(client.draft.text, '你好');
  release({ accepted: true, runId: 'r' }); await Promise.all([first, second]);
  assert.equal(client.draft.text, ''); assert.equal(writes, 1); client.stop();
});
test('late old-session read is ignored and canonical deletions remove visible messages', async () => {
  let resolveOld: (x: any) => void = () => {};
  const client = make(async (route: string) => route.includes('/old/turns') ? new Promise(r => { resolveOld = r; }) : route.includes('/new/turns') ? { messages: [{ id: 'new', content: 'new', ordinal: 1 }], syncCursor: 'x' } : {});
  const old = client.attach('old').catch(e => { assert.equal(e.staleView, true); }); await client.attach('new'); resolveOld({ messages: [{ id: 'old' }] }); await old;
  assert.deepEqual(client.messages.map(x => x.id), ['new']);
  client.mergeMessages([{ id: 'second', ordinal: 2 }], [{ messageId: 'new' }]);
  assert.deepEqual(client.messages.map(x => x.id), ['second']); client.stop();
});
test('approval defaults to return; pasted keys and chat Enter cannot approve', async () => {
  let approvals = 0;
  const item = { id: 'a', kind: 'approval', status: 'pending', request: { command: 'write result.txt', target: 'result.txt' } };
  const client = make(async (route: string) => { if (route.endsWith('/approve')) approvals++; return route.includes('snapshot') ? { approvals: [item] } : {}; });
  client.snapshot = { approvals: [item] }; const surface = new Surface(client);
  surface.inboxItem(item); assert.equal(surface.page?.actions[surface.page.selected].label, '返回');
  await surface.handle({ key: 'paste', text: '\x1b[B\x1b[B\r' }); await surface.handle({ key: 'enter' }); assert.equal(approvals, 0);
  surface.inboxItem(item); await surface.handle({ key: 'down' }); await surface.handle({ key: 'down' }); await surface.handle({ key: 'enter' }); assert.equal(approvals, 1);
  surface.inboxItem({ id: 'opaque' }); assert.equal(surface.page?.actions[2].disabled, true); client.stop();
});
test('stop refuses identity drift and detach never posts a stop command', async () => {
  let posts = 0;
  const client = make(async (_route: string, opts: any) => { if (opts?.method === 'POST') posts++; return { currentRun: { id: 'new-run', status: 'running' } }; });
  await assert.rejects(client.interrupt('old-run'), /状态已变化/);
  client.stop(); assert.equal(posts, 0);
});
test('secret form is not included in drafts and network errors cannot echo secret', async () => {
  const client = make(async () => ({})); const surface = new Surface(client);
  surface.form('凭据', [{ key: 'apiKey', label: '密钥', value: '', secret: true }], async () => { throw new Error('sensitive-secret'); });
  await surface.handle({ key: 'paste', text: 'sensitive-secret' });
  await surface.execute(() => surface.handle({ key: 'f9' })); client.save(true);
  assert.doesNotMatch(client.notice, /sensitive-secret/); assert.doesNotMatch(readFileSync(client.store.file, 'utf8'), /sensitive-secret/);
  await surface.close(true); assert.equal(surface.formEditor.text, ''); client.stop();
});

test('old epoch sync cannot resurrect messages after another client revises the transcript', async () => {
  const client = make(async (route: string) => route.includes('timeline/sync')
    ? { contextEpoch: 1, transcriptRevision: 1, messages: [{ id: 'deleted', content: 'old' }] }
    : { events: [{ seq: 10, topic: 'run.error', contextEpoch: 1 }] });
  client.identity = { contextEpoch: 2, transcriptRevision: 2 };
  client.messages = [{ id: 'current', content: 'new', ordinal: 1 }];
  await client.tick(); assert.deepEqual(client.messages.map(x => x.id), ['current']); assert.equal(client.notice, ''); client.stop();
});

test('new transcript epoch reloads authoritative window and preserves unsent draft', async () => {
  const client = make(async (route: string) => route.includes('timeline/sync')
    ? { contextEpoch: 2, transcriptRevision: 2, messages: [] }
    : route.includes('/turns') ? { contextEpoch: 2, transcriptRevision: 2, messages: [{ id: 'revised', content: 'edited' }], syncCursor: 'new' }
    : route.includes('/snapshot') ? { contextEpoch: 2, transcriptRevision: 2 } : { events: [] });
  client.identity = { contextEpoch: 1, transcriptRevision: 1 }; client.messages = [{ id: 'deleted' }]; client.setDraft('保留草稿');
  await client.tick(); assert.equal(client.messages[0].id, 'revised'); assert.equal(client.draft.text, '保留草稿'); assert.equal(client.identity.contextEpoch, 2); client.stop();
});

test('instance-bound draft files isolate identical session IDs, owner headers and unknown sends', async () => {
  let instance = 'A'; const headers: any[] = [];
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-tui-instances-')));
  const client = new Client(store, async (route, options) => {
    if (route.endsWith('/instance')) return { instanceId: instance };
    headers.push({ instance, headers: options.headers });
    if (route.endsWith('/owner')) return { initialized: true, user: { sessionIdentifier: `owner-${instance}` } };
    if (route.includes('quick-index')) return { sessions: [] };
    return { messages: [], syncCursor: 'cursor' };
  });
  await client.initialize(); await client.attach('same'); client.setDraft('A 私有草稿');
  client.draft.unknown = { clientMessageId: 'uncertain-A', startedAt: 'now' }; client.save(true);
  const aFile = store.file;
  instance = 'B'; await client.initialize(); assert.notEqual(store.file, aFile); assert.equal(client.draft.text, ''); assert.equal(client.draft.unknown, undefined);
  assert.equal(headers.find(x => x.instance === 'B')!.headers['x-v8-agent-os-user-email'], undefined);
  await client.attach('same'); client.setDraft('B 私有草稿'); client.save(true);
  instance = 'A'; await client.initialize(); assert.equal(client.draft.text, 'A 私有草稿'); assert.equal(client.draft.unknown?.clientMessageId, 'uncertain-A');
  await assert.rejects(client.submit(), /待确认/);
  instance = 'B'; await client.initialize(); assert.equal(client.draft.text, 'B 私有草稿'); client.stop();
});

test('legacy unbound draft is preserved on disk and never adopted by another Engine', async () => {
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-tui-unbound-')));
  store.write({ instanceId: '', sessionId: 'old-session', drafts: { 'old-session': { text: 'unknown authority', attachments: [] } }, workspace: '/old', sidebar: false, detail: false, scroll: {} });
  const legacy = store.file;
  const client = new Client(store, async route => route.endsWith('/instance') ? { instanceId: 'new-engine' } : route.endsWith('/owner') ? { initialized: true, user: { sessionIdentifier: 'new-owner' } } : { sessions: [] });
  await client.initialize(); assert.equal(client.view.sessionId, ''); assert.equal(client.draft.text, ''); assert.equal(client.view.workspace, '');
  assert.match(readFileSync(legacy, 'utf8'), /unknown authority/); client.stop();
});

test('late owner response from previous instance cannot replace current principal', async () => {
  let instance = 'A', resolveOwner: (x: any) => void = () => {}; const principals: string[] = [];
  const client = new Client(new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-tui-owner-'))), async (route, options) => {
    if (route.endsWith('/instance')) return { instanceId: instance };
    if (route.endsWith('/owner') && instance === 'A') return new Promise(r => { resolveOwner = r; });
    if (route.endsWith('/owner')) return { initialized: true, user: { sessionIdentifier: 'owner-B' } };
    principals.push(options.headers['x-v8-agent-os-user-email']); return { sessions: [] };
  });
  const a = client.initialize(); await new Promise(r => setImmediate(r)); instance = 'B'; await client.initialize();
  resolveOwner({ initialized: true, user: { sessionIdentifier: 'owner-A' } }); await a;
  await client.listSessions(); assert.equal(principals.at(-1), 'owner-B'); client.stop();
});

test('approval mode is omitted by default and sent only after explicit user selection', async () => {
  const payloads: any[] = [];
  const client = make(async (route: string, options: any) => {
    if (route === '/v1/chat/submit') { payloads.push(options.body); return { accepted: true, runId: 'r' }; }
    return {};
  });
  client.setDraft('Engine mode'); await client.submit(); assert.equal('safetyApprovalMode' in payloads[0].data, false);
  for (const mode of ['manual', 'reduced', 'minimal'] as const) { client.approvalMode = mode; client.setDraft(mode); await client.submit(); assert.equal(payloads.at(-1).data.safetyApprovalMode, mode); }
  client.stop();
});

test('token budgets remain visible and editable while credential fields are blocked', () => {
  const budgets = { globalDailyTokenLimit: 2000, maxTokens: 1000, promptTokens: 40, default_context_window_tokens: 32000 };
  assert.equal(containsSecretField(budgets), false);
  for (const key of Object.keys(budgets)) assert.ok(describe(budgets).some(line => line.includes(key)));
  for (const key of ['apiKey', 'clientSecret', 'refresh_token', 'OPENAI_API_KEY', 'password', 'Authorization']) {
    assert.equal(containsSecretField({ nested: { [key]: 'private' } }), true); assert.equal(describe({ [key]: 'private' }).length, 0);
  }
});
