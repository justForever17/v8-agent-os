import assert from "node:assert/strict";
import test from "node:test";

import { createInitialSessionRealtimeMessageState } from "../dist/cdc.js";
import { buildClientToolSurface, formatClientToolResult } from "../dist/client-tool-surface.js";
import { applyRealtimeEventToMessages } from "../dist/message-lifecycle.js";

function applyToolResult(tool) {
  const initial = createInitialSessionRealtimeMessageState();
  const messages = [];
  const next = applyRealtimeEventToMessages(
    {
      type: "tool_result",
      visibility: "visible",
      targets: ["message"],
      run_id: "run-tool-status",
      node_id: "node-tool-status",
      tool,
    },
    messages,
    initial.currentAiMsg,
    initial.activeAgentProfile,
  );
  const message = messages.find((item) => item.role === "assistant") || next.currentAiMsg;
  return message?.nodes?.find((node) => node.kind === "execution" && node.executionType === "tool_result");
}

test("authoritative command redirect stays waiting through lifecycle and client surface", () => {
  const node = applyToolResult({
    toolCallId: "call-redirect",
    toolName: "run_system_command",
    result: "$ pip install python-docx\n[command_session_required]",
    resultStatus: "waiting",
    resultReasonCode: "command_session_required",
  });

  assert.equal(node?.resultStatus, "waiting");
  assert.equal(node?.resultReasonCode, "command_session_required");
  assert.equal(buildClientToolSurface({
    toolName: node.toolName,
    state: "result",
    result: node.result,
    resultStatus: node.resultStatus,
  }).status, "waiting");
});

test("authoritative terminal statuses override result-event completion fallback", () => {
  for (const status of ["failed", "blocked", "timed_out", "terminated"]) {
    assert.equal(buildClientToolSurface({
      toolName: "run_system_command",
      state: "result",
      result: "tool result received",
      resultStatus: status,
    }).status, status);
  }
});

test("legacy command redirect marker remains waiting when old history has no resultStatus", () => {
  assert.equal(buildClientToolSurface({
    toolName: "run_system_command",
    state: "result",
    result: "$ pip install python-docx\n[command_session_required]",
  }).status, "waiting");
});

test("human tool result redacts local paths without removing parsed document content", () => {
  const result = formatClientToolResult([
    "--- File: C:\\Users\\Lenovo\\.v8-agent-os\\workspace\\report.docx (Lines 1 to 4 of 4) ---",
    "# 信息科每日工作汇总",
    "| 工作事项 | 负责人 |",
    "| 机房巡检 | 张三 |",
  ].join("\n"));

  assert.doesNotMatch(result, /C:\\Users\\Lenovo/);
  assert.match(result, /\[local path\]/);
  assert.match(result, /机房巡检/);
  assert.match(result, /负责人/);
});
