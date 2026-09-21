import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Client, idOf } from '../src/client.js';
import { ViewStore } from '../src/persistence.js';
import { Surface } from '../src/surface.js';
import { buildQuestionAnswer, createQuestionDraft, normalizeQuestions, questionAnswered } from '../src/inbox.js';

function make(transport: any) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'v8-inbox-test-'));
  const client = new Client(new ViewStore(root, 'fixture'), transport);
  client.instance = { instanceId: 'fixture' }; client.connection = '已连接'; client.view.sessionId = 's';
  return { client, root };
}

test('structured questions preserve original labels and serialize like Web/Phone', () => {
  const item = { id: 'q1', request: { question: '请选择', questions: [
    { id: 'style', title: '风格', options: [{ id: 'a', title: '简洁', detail: '清爽' }, { id: 'b', title: '复杂' }] },
    { id: 'targets', title: '目标', multiSelect: true, options: [{ id: 'web', title: 'Web' }, { id: 'phone', title: 'Phone' }] },
  ] } };
  const questions = normalizeQuestions(item), draft = createQuestionDraft();
  draft.selected.style = ['a']; draft.selected.targets = ['web', 'phone'];
  assert.equal(questionAnswered(questions[0], 0, draft), true);
  assert.equal(buildQuestionAnswer(questions, draft), '1. 风格: 简洁\n2. 目标: Web；Phone');
  assert.equal(normalizeQuestions({ id: 'q2', request: { question: '原始问题' } })[0].title, '原始问题');
});

test('idOf recognizes interaction and approval projection identities', () => {
  assert.equal(idOf({ interactionId: 'ask-1', sessionId: 's' }), 'ask-1');
  assert.equal(idOf({ approvalId: 'approval-1', sessionId: 's' }), 'approval-1');
});

test('ask_user decide sends Engine RunCommandPayload response.answer and rechecks pending target', async () => {
  const requests: any[] = [];
  const item = { id: 'ask-1', kind: 'question', status: 'pending', sessionId: 's', request: { question: '继续？' } };
  const { client, root } = make(async (route: string, options: any = {}) => {
    requests.push({ route, options });
    if (route.includes('/snapshot')) return { askUserInteractions: [item] };
    return { interaction: { id: 'ask-1', status: 'resolved' } };
  });
  client.snapshot = { askUserInteractions: [item] };
  await client.decide(item, 'answer', '继续');
  assert.deepEqual(requests.find((entry: any) => entry.route.endsWith('/respond')).options.body, { response: { answer: '继续' } });
  await assert.rejects(client.decide({ id: 'missing', kind: 'question', sessionId: 's' }, 'answer', 'x'), /过期|已处理/);
  client.stop(); rmSync(root, { recursive: true, force: true });
});

test('question page exposes option selection, custom answer, and non-destructive return', async () => {
  const item = { id: 'ask-1', kind: 'question', status: 'pending', sessionId: 's', request: { question: '选择', questions: [{ id: 'q', title: '方向', options: [{ id: 'one', title: '一' }, { id: 'two', title: '二' }] }] } };
  const { client, root } = make(async (route: string) => route.includes('/snapshot') ? { askUserInteractions: [item] } : route.includes('/approvals') ? { approvals: [] } : route.endsWith('/owner') ? { initialized: true, user: { sessionIdentifier: 'owner' } } : { items: [] });
  client.snapshot = { askUserInteractions: [item] };
  const surface = new Surface(client);
  surface.inboxItem(item);
  assert.equal(surface.page?.title, '回答提问');
  assert.ok(surface.page?.actions.some(action => action.label === '一'));
  const back = surface.page!.actions[0]; await back.run();
  assert.equal(surface.page?.title, '待处理');
  client.stop(); rmSync(root, { recursive: true, force: true });
});
