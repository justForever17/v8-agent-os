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

test("subagent projection removes raw role transcript noise from the human conclusion", () => {
  const projection = buildSubagentReturnProjection([{ nodes: [{
    kind: "execution",
    topic: "subagent.task.completed",
    data: {
      delegationId: "delegation-transcript",
      subagentName: "Worker",
      resultText: [
        "ai: 使用工具: read_native_file Reading README.md.",
        "tool: 使用工具: read_native_file --- File: README.md --- raw payload",
        "assistant: 首个标题是 ## What Is V8 Agent OS?，第 13 行。",
      ].join("\n"),
    },
  }] }]);
  assert.equal(projection[0].summary, "首个标题是 ## What Is V8 Agent OS?，第 13 行。");

  const toolOnlyProjection = buildSubagentReturnProjection([{ nodes: [{
    kind: "execution",
    topic: "subagent.task.completed",
    data: {
      delegationId: "delegation-tool-only-transcript",
      subagentName: "Worker",
      compactTranscript: "ai: 使用工具: inspect Checking.\ntool: 使用工具: inspect raw payload",
    },
  }] }]);
  assert.equal(toolOnlyProjection[0].summary, null);
});

test("subagent returns can be restored from the durable runtime timeline without leaking raw reasoning", () => {
  const projection = buildSubagentReturnProjection([], [{
    id: "runtime-subagent-event",
    kind: "execution",
    topic: "subagent.task.completed",
    timestamp: 20,
    data: {
      delegationId: "delegation-runtime",
      delegationDepth: 1,
      subagentName: "Creative Media Director",
      subagentFamily: "creative_media",
      taskGoal: "Check the collaboration surface",
      resultText: "AUTHORITY_OK",
      compactTranscript: "<think>provider reasoning</think>\ntoolobs://secret-runtime-detail",
      localSelfCheck: "Subagent branch completed; supervisor must still accept, retry, or ignore the result.",
      acceptanceHint: "Review the result.",
      supervisorAcceptance: { status: "pending" },
    },
  }]);

  assert.equal(projection.length, 1);
  assert.equal(projection[0].name, "Creative Media Director");
  assert.equal(projection[0].taskGoal, "Check the collaboration surface");
  assert.equal(projection[0].summary, "AUTHORITY_OK");
  assert.equal(projection[0].selfCheck, null);
});

test("subagent activity starts from the durable episode and keeps fine-grained eventSeq order", () => {
  const projection = buildSubagentReturnProjection([], [
    {
      id: "tool-finished",
      eventId: "tool-finished",
      eventSeq: 14,
      kind: "execution",
      topic: "subagent.tool.finished",
      data: {
        ownerRuntimeId: "subagent_swarm",
        ownerAgentKind: "subagent",
        ownerAgentId: "reviewer",
        runtimeContext: { delegation_id: "delegation-live", run_id: "run_hidden123456" },
        tool: { toolName: "inspect", toolCallId: "tool-1", agentVisibleResult: { ok: true, run_id: "run_hidden123456", summary: "Evidence confirmed" } },
      },
    },
    {
      id: "episode-started",
      eventId: "episode-started",
      eventSeq: 10,
      kind: "execution",
      topic: "runtime.episode.started",
      data: {
        episode: {
          episodeId: "delegation-live",
          kind: "delegation",
          targetKind: "local_agent",
          targetLabel: "Reviewer",
          state: "active",
          inputs: { taskBrief: { goal: "Verify the result" } },
        },
      },
    },
    {
      id: "reasoning",
      eventId: "reasoning",
      eventSeq: 12,
      kind: "execution",
      topic: "subagent.reasoning.delta",
      content: "Checking the evidence.",
      data: {
        ownerRuntimeId: "subagent_swarm",
        ownerAgentKind: "subagent",
        ownerAgentId: "reviewer",
        runtimeContext: { delegation_id: "delegation-live" },
      },
    },
    {
      id: "tool-started",
      eventId: "tool-started",
      eventSeq: 13,
      kind: "execution",
      topic: "subagent.tool.started",
      data: {
        ownerRuntimeId: "subagent_swarm",
        ownerAgentKind: "subagent",
        ownerAgentId: "reviewer",
        runtimeContext: { delegation_id: "delegation-live" },
        tool: { toolName: "inspect", toolCallId: "tool-1", args: { path: "README.md" } },
      },
    },
  ]);

  assert.equal(projection.length, 1);
  assert.equal(projection[0].name, "Reviewer");
  assert.equal(projection[0].taskGoal, "Verify the result");
  assert.deepEqual(projection[0].events.map((event) => event.eventSeq), [10, 12, 13, 14]);
  assert.equal(projection[0].events[0].kind, "started");
  assert.equal(projection[0].events[0].node.topic, undefined);
  assert.equal(projection[0].events[0].node.runId, undefined);
  const result = projection[0].events.at(-1).node.result;
  assert.deepEqual(result, { ok: true, summary: "Evidence confirmed" });
});
