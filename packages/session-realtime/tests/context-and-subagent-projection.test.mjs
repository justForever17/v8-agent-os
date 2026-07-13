import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSubagentReturnProjection,
  contextUsagePercent,
} from "../dist/index.js";

test("context usage prefers the post-compaction model-aware estimate", () => {
  assert.equal(contextUsagePercent({
    contextWindowTokens: 200_000,
    estimatedInputTokens: 180_000,
    effectiveInputTokens: 74_000,
  }), 37);
  assert.equal(contextUsagePercent({
    context_window_tokens: 128_000,
    estimated_input_tokens: 32_000,
  }), 25);
  assert.equal(contextUsagePercent({ contextWindowTokens: 0, effectiveInputTokens: 1 }), null);
});

test("subagent returns keep direct lineage and nest grandchild truth without an avatar", () => {
  const projection = buildSubagentReturnProjection([
    {
      nodes: [
        {
          id: "parent-event",
          kind: "execution",
          topic: "subagent.task.completed",
          timestamp: 10,
          data: {
            delegationId: "delegation-parent",
            invocationId: "invocation-parent",
            delegationDepth: 1,
            subagentName: "Codebase Researcher",
            subagentFamily: "research",
            subagentAvatar: "https://example.test/avatar.png",
            taskGoal: "Find the cause",
            compactTranscript: "Root cause confirmed.",
            localSelfCheck: "Evidence matched.",
            artifactRefs: ["artifact://proof"],
            supervisorAcceptance: { status: "pending" },
          },
        },
        {
          id: "child-event",
          kind: "execution",
          topic: "subagent.task.completed",
          timestamp: 11,
          data: {
            delegationId: "delegation-child",
            parentDelegationId: "delegation-parent",
            delegationDepth: 2,
            subagentName: "Scanner",
            subagentAvatar: "https://example.test/should-not-project.png",
            compactTranscript: "Nested evidence returned.",
          },
        },
      ],
    },
  ]);

  assert.equal(projection.length, 1);
  assert.equal(projection[0].name, "Codebase Researcher");
  assert.equal(projection[0].avatar, "https://example.test/avatar.png");
  assert.deepEqual(projection[0].artifactRefs, ["artifact://proof"]);
  assert.equal(projection[0].children.length, 1);
  assert.equal(projection[0].children[0].name, "Scanner");
  assert.equal(projection[0].children[0].avatar, null);
  assert.equal(projection[0].children[0].summary, "Nested evidence returned.");
});

test("subagent projection drops raw JSON-shaped payload noise", () => {
  const projection = buildSubagentReturnProjection([{ nodes: [{
    kind: "execution",
    topic: "subagent.task.failed",
    data: {
      delegationId: "delegation-noise",
      subagentName: "Worker",
      compactTranscript: '{"raw":"provider payload"}',
    },
  }] }]);
  assert.equal(projection[0].summary, null);
});
