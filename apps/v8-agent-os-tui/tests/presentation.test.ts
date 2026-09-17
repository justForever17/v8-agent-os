import assert from 'node:assert/strict';
import test from 'node:test';
import { executionSummaries, messageText, PausedTranscriptUpdates } from '../src/presentation.js';

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
