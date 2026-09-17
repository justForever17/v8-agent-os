import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildAssistantMessage, createInitialSessionRealtimeMessageState,
  flushQueuedSessionRealtimeRuntimeEvents, normalizeSessionRuntimeEvent,
  queueSessionRealtimeRuntimeEvent,
} from '../dist/index.js';

function pending(id, runId) {
  return { ...buildAssistantMessage({}, runId), metadata: { clientMessageId: id } };
}
function recorded(id, runId, seq = 1) {
  return normalizeSessionRuntimeEvent({ topic: 'message.user.recorded', seq, run_id: runId,
    payload: { message_id: id, clientMessageId: id, content: 'Hello' } });
}
function text(runId, content, seq = 3) {
  return normalizeSessionRuntimeEvent({ topic: 'run.text.delta', seq, run_id: runId,
    message_id: `answer-${runId}`, payload: { content } });
}
function flush(messages, events) {
  const state = createInitialSessionRealtimeMessageState(messages);
  for (const event of events) queueSessionRealtimeRuntimeEvent(state, event);
  return flushQueuedSessionRealtimeRuntimeEvents(messages, state).messages;
}

test('a recorded user and first text in one frame adopt the matching placeholder', () => {
  const assistant = pending('client-A');
  const messages = flush([{ id: 'client-A', role: 'user', content: 'Hello' }, assistant],
    [recorded('client-A', 'run-A'), text('run-A', 'First answer')]);
  assert.equal(messages.filter(m => m.role === 'assistant').length, 1);
  const answer = messages.find(m => m.role === 'assistant');
  assert.equal(answer.renderKey, assistant.renderKey);
  assert.equal(answer.runId, 'run-A');
  assert.equal(answer.content, 'First answer');
});

test('a late older run cannot adopt or write into the pending next submission', () => {
  const old = { ...pending('client-A', 'run-A'), id: 'answer-run-A', content: 'Old', uiEphemeral: false, uiStreamPhase: 'completed' };
  const next = pending('client-B');
  const messages = flush([{ id: 'client-A', role: 'user', content: 'Hello' }, old,
    { id: 'client-B', role: 'user', content: 'Next' }, next],
    [recorded('client-A', 'run-A'), text('run-A', '-tail'), recorded('client-B', 'run-B', 4), text('run-B', 'New answer', 5)]);
  assert.equal(messages.filter(m => m.role === 'assistant').length, 2);
  assert.equal(messages.find(m => m.runId === 'run-B' && m.role === 'assistant').renderKey, next.renderKey);
  assert.equal(messages.find(m => m.runId === 'run-B' && m.role === 'assistant').content, 'New answer');
  assert.equal(messages.find(m => m.id === 'answer-run-A').content, 'Old-tail');
});

test('queued next user does not redirect the active run text, duplicate user receipts are idempotent', () => {
  const active = pending('client-A', 'run-A');
  const events = [recorded('client-B', 'run-B'), recorded('client-B', 'run-B', 2), text('run-A', 'Still A')];
  const messages = flush([{ id: 'client-A', role: 'user', content: 'Hello' }, active], events);
  assert.equal(messages.filter(m => m.id === 'client-B').length, 1);
  assert.equal(messages.filter(m => m.role === 'assistant').length, 1);
  assert.equal(messages.find(m => m.role === 'assistant').runId, 'run-A');
  assert.equal(messages.find(m => m.role === 'assistant').renderKey, active.renderKey);
});

test('a late old agent-start updates its own run, never the next accepted placeholder', () => {
  const old = {...pending('client-A', 'run-A'), id: 'answer-run-A', uiEphemeral: false, uiStreamPhase: 'completed'};
  const next = pending('client-B', 'run-B');
  const messages = flush([old, {id:'client-B',role:'user',content:'Next',runId:'run-B'}, next], [
    {type:'agent_start',run_id:'run-A',message_id:'answer-run-A',targets:['message'],agent:{name:'Supervisor'}},
    text('run-B','Only B'),
  ]);
  const answer = messages.find(m=>m.runId==='run-B'&&m.role==='assistant');
  assert.equal(answer.renderKey,next.renderKey);
  assert.equal(answer.content,'Only B');
  assert.equal(messages.find(m=>m.id==='answer-run-A').runId,'run-A');
});
