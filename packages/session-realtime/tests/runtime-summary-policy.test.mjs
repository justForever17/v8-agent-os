import assert from "node:assert/strict";
import test from "node:test";

import { shouldProjectRuntimeSummarySignal } from "../dist/runtime-summary-policy.js";

test("runtime status projection excludes message-level reasoning and tool noise", () => {
  for (const signal of [
    { topic: "run.reasoning.delta", kind: "progress" },
    { topic: "run.text.delta", kind: "progress" },
    { topic: "tool.started", kind: "progress" },
    { topic: "research.tool.finished", kind: "tool" },
    { executionType: "reasoning", kind: "progress" },
    { executionType: "tool_call", kind: "progress" },
  ]) {
    assert.equal(shouldProjectRuntimeSummarySignal(signal), false);
  }
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
