import assert from "node:assert/strict";

import {
  applyRealtimeEventToMessages,
} from "../dist/message-lifecycle.js";
import { createInitialSessionRealtimeMessageState } from "../dist/cdc.js";
import { buildMessageTimelineSegments } from "../dist/message-segments.js";

const lifecycleOptions = {
  createId(prefix) {
    lifecycleOptions.counter += 1;
    return `${prefix}-${lifecycleOptions.counter}`;
  },
  counter: 0,
};

function apply(state, event) {
  const nextStreamState = applyRealtimeEventToMessages(
    { visibility: "visible", ...event },
    state.messages,
    state.currentAiMsg,
    state.activeAgentProfile,
    lifecycleOptions,
  );
  state.currentAiMsg = nextStreamState.currentAiMsg;
  state.activeAgentProfile = nextStreamState.activeAgentProfile;
}

const state = {
  messages: [],
  ...createInitialSessionRealtimeMessageState(),
};

apply(state, {
  type: "reasoning_chunk",
  run_id: "run_timeline",
  runtimeId: "chat",
  targets: ["message"],
  content: "先判断。",
  node_id: "reasoning-1",
  data: { ownerStreamKey: "trace-1", traceGroupId: "stable-trace-1" },
});

apply(state, {
  type: "text_chunk",
  run_id: "run_timeline",
  runtimeId: "chat",
  targets: ["message"],
  content: "第一段正文。",
  node_id: "text-1",
  data: { snapshot: "第一段正文。", finalized: true, ownerStreamKey: "text-1" },
});

apply(state, {
  type: "tool_start",
  run_id: "run_timeline",
  runtimeId: "chat",
  targets: ["message"],
  node_id: "tool-1",
  tool: { toolCallId: "call_1", toolName: "web_broker", args: { mode: "search" } },
  data: { ownerStreamKey: "trace-2", traceGroupId: "stable-trace-2" },
});

apply(state, {
  type: "text_chunk",
  run_id: "run_timeline",
  runtimeId: "chat",
  targets: ["message"],
  content: "第二段正文。",
  node_id: "text-2",
  data: { snapshot: "第二段正文。", finalized: true, ownerStreamKey: "text-2" },
});

apply(state, {
  type: "text_chunk",
  run_id: "run_timeline",
  runtimeId: "chat",
  targets: ["message"],
  content: "第一段正文被错误覆盖。",
  node_id: "text-1",
  data: { snapshot: "第一段正文被错误覆盖。", finalized: true, ownerStreamKey: "text-1" },
});

apply(state, {
  type: "text_chunk",
  run_id: "run_timeline",
  runtimeId: "research",
  targets: ["message"],
  content: "这段不应进入主消息。",
  node_id: "research-text",
  data: { ownerRuntimeId: "research", displayInMessage: false },
});

const assistant = state.messages.find((message) => message.role === "assistant");
assert.ok(assistant, "assistant message should exist");
assert.ok(Array.isArray(assistant.nodes), "assistant nodes should exist");

const nodeKinds = assistant.nodes.map((node) => node.kind === "execution" ? `${node.kind}:${node.executionType}` : node.kind);
assert.deepEqual(nodeKinds.slice(0, 4), [
  "execution:reasoning",
  "narrative",
  "execution:tool_call",
  "narrative",
]);

const firstText = assistant.nodes.find((node) => node.id === "text-1");
assert.equal(firstText?.content, "第一段正文。", "finalized text node must not be overwritten");
assert.ok(
  assistant.nodes.some((node) => String(node.id || "").startsWith("text-1:append:")),
  "changed update for a finalized node should append instead of overwriting",
);
assert.ok(
  !assistant.nodes.some((node) => String(node.content || "").includes("这段不应进入主消息")),
  "runtime text without message surface must stay out of the main message",
);

const visibleNodes = assistant.nodes.filter((node) => !String(node.id || "").includes(":append:"));
const segments = buildMessageTimelineSegments(visibleNodes, { active: false });
assert.deepEqual(segments.map((segment) => segment.kind), [
  "trace_group",
  "node",
  "trace_group",
  "node",
]);
assert.equal(segments[0].collapsedByDefault, true, "completed trace before text should collapse");
assert.equal(segments[2].collapsedByDefault, true, "completed tool trace before text should collapse");
assert.equal(segments[0].id, "stable-trace-1", "explicit traceGroupId should be stable");
assert.equal(segments[2].id, "stable-trace-2", "explicit traceGroupId should be stable");

const activeSegments = buildMessageTimelineSegments([
  { id: "text-final", kind: "narrative", content: "正文。", timestamp: 1 },
  { id: "tool-active", kind: "execution", executionType: "tool_call", timestamp: 2 },
], { active: true });
assert.equal(activeSegments[1].kind, "trace_group");
assert.equal(activeSegments[1].collapsedByDefault, false, "active trailing trace should stay expanded");

console.log(JSON.stringify({
  ok: true,
  nodeKinds,
  segmentKinds: segments.map((segment) => segment.kind),
}, null, 2));
