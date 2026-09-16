import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Client } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { Surface } from '../src/surface.js';

function make(api: any) {
  const store = new ViewStore(mkdtempSync(path.join(os.tmpdir(), 'v8-tui-test-')));
  const client = new Client(store, api); client.view.sessionId = 's'; client.syncCursor = '2026-09-17T00:00:00Z';
  return client;
}
test('lost submit response persists unknown intent and blocks repetition after restart', async () => {
  let writes = 0;
  const client = make(async (route: string) => { if (route === '/v1/chat/submit') { writes++; throw new Error('response lost'); } return {}; });
  client.setDraft('请执行一次'); await client.submit();
  assert.equal(writes, 1); assert.equal(client.draft.text, '请执行一次'); assert.ok(client.draft.unknown);
  const next = new Client(client.store, client.api); await assert.rejects(next.submit(), /待确认/); assert.equal(writes, 1);
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
  const old = client.attach('old'); await client.attach('new'); resolveOld({ messages: [{ id: 'old' }] }); await old;
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
