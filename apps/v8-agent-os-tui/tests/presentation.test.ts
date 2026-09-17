import assert from 'node:assert/strict';
import test from 'node:test';
import { executionSummaries, messageText, readerMessageUpdate, PausedTranscriptUpdates } from '../src/presentation.js';

test('a completed invocation replaces its running marker without merging another same-name invocation', () => {
  const nodes = [
    { kind: 'execution', executionType: 'tool_call', toolCallId: 'a', toolName: 'read_native_file', status: 'running' },
    { kind: 'execution', executionType: 'tool_result', toolCallId: 'a', toolName: 'read_native_file', resultStatus: 'succeeded', result: { summary: '已读取工作区文件' }, detailRef: 'detail:a' },
    { kind: 'execution', executionType: 'tool_call', toolCallId: 'b', toolName: 'read_native_file', status: 'running' },
    { kind: 'execution', executionType: 'agent_start' },
  ];
  const rows = executionSummaries({ nodes });
  assert.equal(rows.length, 2);
  assert.match(rows[0], /已完成.*已读取工作区文件/); assert.doesNotMatch(rows[0], /detail:a/);
  assert.equal(nodes[1].detailRef, 'detail:a'); // Diagnostics owner keeps the reference intact.
  assert.doesNotMatch(rows[0], /运行中/); assert.match(rows[1], /运行中/);
  assert.deepEqual(executionSummaries({ nodes: [nodes[1], nodes[0], nodes[2]] }), rows);
});

test('unknown and failed outcomes remain visible, with useful summary and no raw credentials', () => {
  const text = messageText({ role: 'assistant', state: 'completed', content: '已收到回执。', nodes: [
    { kind: 'execution', executionType: 'tool_result', toolCallId: 'x', toolName: 'device_broker', resultStatus: 'unknown', result: { summary: '连接中断，动作结果未确认', credential: 'not-for-human' } },
    { kind: 'execution', executionType: 'tool_result', toolCallId: 'y', toolName: 'run_system_command', resultStatus: 'failed', result: { summary: '命令执行失败', error: '退出码 1' } },
  ] });
  assert.match(text, /device_broker · 结果待确认/); assert.match(text, /连接中断/);
  assert.match(text, /run_system_command · 失败/); assert.doesNotMatch(text, /not-for-human|credential|agent_start/);
});

test('paused history counts a streaming message once, includes new messages and clears only on follow', () => {
  const activity = new PausedTranscriptUpdates();
  const first = { id: 'a', content: '原文' };
  assert.equal(activity.update([first], true), 0);
  assert.equal(activity.update([first], false), 0);
  assert.equal(activity.update([{ ...first, content: '原文新' }], false), 1);
  assert.equal(activity.update([{ ...first, content: '原文新正文' }], false), 1);
  assert.equal(activity.update([{ ...first, content: '原文新正文' }, { id: 'b', content: '下一条' }], false), 2);
  assert.equal(activity.update([{ id: 'b', content: '下一条' }], false), 1);
  assert.equal(activity.update([{ id: 'b', content: '下一条' }], true), 0);
});

test('linear reader appends real projected prose once despite layout newline', () => {
  const base = '较长的已有正文。'.repeat(1000);
  let previous = messageText({ role: 'assistant', state: 'streaming', content: base });
  let output = readerMessageUpdate(undefined, previous);
  const additions = ['新增', '中文', '👩🏽‍💻', '\n', '\n', '结尾'];
  let body = base;
  for (const addition of additions) {
    body += addition;
    const next = messageText({ role: 'assistant', state: 'streaming', content: body });
    assert.equal(readerMessageUpdate(previous, next), addition);
    output += readerMessageUpdate(previous, next);
    previous = next;
  }
  assert.equal(output, `\n主理人 · 正在回复\n${body}`);
  assert.equal(readerMessageUpdate(previous, previous), '');
});

test('linear reader announces changed status without repeating prose and explicitly corrects revisions', () => {
  const running = messageText({ role: 'assistant', state: 'streaming', content: '原文' });
  const completed = messageText({ role: 'assistant', state: 'completed', content: '原文结束' });
  assert.equal(readerMessageUpdate(running, completed), '结束\n主理人 · 已完成\n');
  const revised = messageText({ role: 'assistant', state: 'completed', content: '已修订' });
  assert.equal(readerMessageUpdate(completed, revised), '\n消息已更新\n主理人 · 已完成\n已修订');
  const removed = messageText({ role: 'assistant', state: 'failed', content: '' });
  assert.equal(readerMessageUpdate(revised, removed), '\n消息已更新\n主理人 · 失败\n');
});

test('linear reader never hides a changed tool outcome or the first real content after an empty shell', () => {
  const empty = messageText({ role: 'assistant', state: 'pending', content: '' });
  const real = messageText({ role: 'assistant', state: 'streaming', content: '你好' });
  assert.match(readerMessageUpdate(empty, real), /你好/);
  const call = { kind: 'execution', executionType: 'tool_result', toolCallId: 'a', toolName: 'device_broker', resultStatus: 'completed', result: { summary: '原回执' } };
  const before = messageText({ role: 'assistant', nodes: [call] });
  const after = messageText({ role: 'assistant', nodes: [{ ...call, resultStatus: 'unknown', result: { summary: '动作未确认' } }] });
  const update = readerMessageUpdate(before, after);
  assert.match(update, /消息已更新/); assert.match(update, /结果待确认.*动作未确认/);
  assert.doesNotMatch(update, /已完成/);
});
