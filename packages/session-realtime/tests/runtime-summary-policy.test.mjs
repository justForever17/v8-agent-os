import assert from "node:assert/strict";
import test from "node:test";

import { humanizeRuntimeSummaryText, shouldProjectRuntimeSummarySignal } from "../dist/runtime-summary-policy.js";

test("runtime status projection excludes message-level reasoning and tool noise", () => {
  for (const signal of [
    { topic: "run.reasoning.delta", kind: "progress" },
    { topic: "run.text.delta", kind: "progress" },
    { topic: "tool.started", kind: "progress" },
    { topic: "research.tool.finished", kind: "tool" },
    { executionType: "reasoning", kind: "progress" },
    { executionType: "tool_call", kind: "progress" },
    { topic: "runtime.lease.heartbeat", kind: "progress" },
    { topic: "runtime.episode.progress", kind: "progress" },
    { topic: "run.checkpoint.saved", kind: "progress" },
    { topic: "runtime.episode.handoff_resume_not_scheduled", kind: "diagnostic" },
  ]) {
    assert.equal(shouldProjectRuntimeSummarySignal(signal), false);
  }
});

test("runtime summaries hide internal identifiers from human surfaces", () => {
  assert.equal(
    humanizeRuntimeSummaryText("Run run_1234567890abcdef entered episode_deadbeef12345678", "en"),
    "Run current task entered execution stage",
  );
  assert.equal(
    humanizeRuntimeSummaryText("运行 run_1234567890abcdef 已进入 episode_deadbeef12345678", "zh-CN"),
    "运行 当前任务 已进入 执行阶段",
  );
});

test("runtime status projection keeps phase summaries, governance, handoffs and artifacts", () => {
  for (const signal of [
    { topic: "runtime.episode.completed", kind: "progress" },
    { topic: "approval.requested", kind: "governance" },
    { topic: "subagent.started", kind: "handoff" },
    { topic: "artifact.recorded", kind: "artifact" },
  ]) {
    assert.equal(shouldProjectRuntimeSummarySignal(signal), true);
  }
});
