import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Client } from '../src/client.js';
import { Surface } from '../src/surface.js';
import { ViewStore } from '../src/persistence.js';
import { validatedSpecReplacement, verifiedSpecDocument } from '../src/spec-review.js';

const document = (content = '\uFEFF# 设置\r\n\r\n完整中文正文\r\nEND_OF_DOCUMENT\r\n') => ({ content,
  documentSha256: createHash('sha256').update(content, 'utf8').digest('hex'),
  documentPath: '.v8/specs/spec-a/requirements.md', truncated: false });
const card = (doc = document(), id = 'approval-a') => ({ id, kind: 'approval', approval_kind: 'spec_stage_approval', status: 'pending', session_id: 's', run_id: 'run-a', request: {
  specId: 'spec-a', stage: 'requirements', workspacePath: '/synthetic/workspace', documentPath: doc.documentPath, documentSha256: doc.documentSha256, summary: 'Review the requirements',
} });
function make(t: any, transport: any) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-tui-spec-'));
  const client = new Client(new ViewStore(root, 'fixture'), transport);
  client.instance = { instanceId: 'fixture' }; client.connection = '已连接'; client.view.sessionId = 's'; client.sessions = [{ id: 's' }];
  t.after(() => { client.stop(); rmSync(root, { recursive: true, force: true }); });
  return { client, ui: new Surface(client) };
}

test('Spec review verifies full UTF-8 document bytes without normalizing BOM or CRLF', () => {
  const value = document();
  assert.equal(verifiedSpecDocument(value).content, value.content);
  assert.throws(() => verifiedSpecDocument({ ...value, truncated: true }), /完整/);
  assert.throws(() => verifiedSpecDocument({ ...value, content: value.content.replaceAll('\r\n', '\n') }), /不一致/);
  assert.throws(() => verifiedSpecDocument({ ...value, documentPath: '' }), /完整/);
});

test('Spec card requires reading the full document and sends its hash only after explicit approve', async t => {
  const doc = document(), item = card(doc), writes: any[] = []; let pending = [item];
  const { client, ui } = make(t, async (route: string, options: any = {}) => {
    if (options.method) {
      writes.push({ route, body: options.body }); pending = [];
      return { approval: { ...item, status: 'approved' }, spec_stage_approval: { ok: true } };
    }
    if (route.startsWith('/v1/specs/')) { assert.ok(route.includes('full_content=true')); return { ok: true, stages: { requirements: doc } }; }
    if (route.includes('/snapshot')) return { approvals: pending };
    return { approvals: pending };
  });
  client.snapshot = { approvals: pending };
  await ui.inboxItem(item);
  assert.equal(ui.page!.title, 'Spec 文档审批');
  assert.ok(ui.page!.lines.join('\n').includes('END_OF_DOCUMENT'));
  assert.equal(ui.page!.localizeLines, false);
  assert.equal(writes.length, 0);
  await ui.page!.actions.find(x => x.label === '已阅读并批准此版本')!.run();
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0], { route: '/v1/approvals/approval-a/approve', body: { response: { documentSha256: doc.documentSha256 } } });
  assert.equal(client.notice, 'Engine 已确认处理结果。');
});

test('Spec approve cannot bypass full-document review or approve another version', async t => {
  const item = card(); let writes = 0;
  const { client } = make(t, async (_route: string, options: any = {}) => { if (options.method) writes++; return { approvals: [item] }; });
  await assert.rejects(client.decide(item, 'approve'), /先读取/);
  await assert.rejects(client.decide(item, 'approve', '', document('different version')), /先读取/);
  assert.equal(writes, 0);
});

test('stale Spec card refreshes to reviewed version and requires another explicit approval', async t => {
  const old = card(), doc = document('new version\n'), next = card(doc, 'approval-new'); let pending = [old]; const writes: any[] = [];
  const { client, ui } = make(t, async (route: string, options: any = {}) => {
    if (options.method) {
      writes.push({ route, body: options.body });
      assert.ok(route.endsWith('/refresh-spec-review'));
      pending = [next]; return { approval: next, replacesApprovalId: old.id };
    }
    if (route.startsWith('/v1/specs/')) return { ok: true, stages: { requirements: doc } };
    return { approvals: pending };
  });
  client.snapshot = { approvals: pending };
  await ui.inboxItem(old);
  assert.ok(!ui.page!.actions.some(x => x.label === '已阅读并批准此版本'));
  await ui.page!.actions.find(x => x.label.includes('刷新审批'))!.run();
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].body, { response: { documentSha256: doc.documentSha256 } });
  assert.ok(ui.page!.actions.some(x => x.label === '已阅读并批准此版本'));
});

test('Spec replacement rejects task drift, and HTTP success without approved state is not success', async t => {
  const doc = document(), item = card(doc), next = card(doc, 'approval-next');
  assert.throws(() => validatedSpecReplacement({ approval: { ...next, run_id: 'other-run' }, replacesApprovalId: item.id }, item, doc), /不匹配/);
  const { client } = make(t, async (_route: string, options: any = {}) => options.method ? { ok: true } : { approvals: [item] });
  await assert.rejects(client.decide(item, 'approve', '', doc), /尚未确认/);
  assert.notEqual(client.notice, 'Engine 已确认处理结果。');
});

test('leaving while Spec loads cannot reopen the stale document page', async t => {
  let release: (value: any) => void = () => {};
  const { ui } = make(t, async () => new Promise(resolve => { release = resolve; }));
  const work = ui.execute(() => ui.inboxItem(card()));
  await ui.dispatch({ key: 'escape' });
  release({ ok: true, stages: { requirements: document() } });
  await work;
  assert.equal(ui.page, null);
});

test('unreadable or truncated Spec still offers retry, reject and return without an approve action', async t => {
  const { ui } = make(t, async () => ({ ok: true, stages: { requirements: { ...document(), truncated: true } } }));
  await ui.inboxItem(card());
  assert.equal(ui.page!.title, 'Spec 文档未能读取');
  assert.deepEqual(ui.page!.actions.map(x => x.label), ['返回待处理', '重新读取文档', '拒绝此审批']);
});
