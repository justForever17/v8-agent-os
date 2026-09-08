import assert from "node:assert/strict";
import test from "node:test";

import { buildMessageTimelineSegments, coalesceNarrativeFragments } from "../dist/message-segments.js";

const fragment = (content, seq, extra = {}) => ({
  id: `text-${seq}`, kind: "narrative", role: "assistant", content,
  ownerStreamKey: `chat:supervisor:text:model-run:segment:${seq}`,
  ownerRuntimeId: "chat", ownerAgentId: "supervisor", finalized: true, ...extra,
});

test("transport splits preserve one Markdown document during live append and history reload", () => {
  const chunks = ["## Result\n\n```text\n", "approved", "\n```\n\n- Path: `", "note.txt`\n- Size: **8**\n\n", "| Phase | Result |\n|---|---|\n| 2 | approved |"];
  const nodes = chunks.map((text, index) => fragment(text, index + 1));
  const original = structuredClone(nodes);
  for (let count = 1; count <= nodes.length; count++) {
    const live = coalesceNarrativeFragments(nodes.slice(0, count));
    assert.equal(live.length, 1);
    assert.equal(live[0].content, chunks.slice(0, count).join(""));
    assert.equal(live[0].id, "text-1");
  }
  const restored = buildMessageTimelineSegments(JSON.parse(JSON.stringify(nodes)));
  assert.equal(restored.length, 1);
  assert.equal(restored[0].node.content, chunks.join(""));
  assert.deepEqual(nodes, original, "presentation must not rewrite persisted fragments");
});

test("coalescing respects execution, model, actor, missing and duplicate fragment boundaries", () => {
  const first = fragment("first", 1);
  for (const second of [
    fragment("second", 3), fragment("snapshot", 1),
    fragment("second", 2, { ownerAgentId: "worker" }),
    fragment("second", 2, { ownerStreamKey: "chat:supervisor:text:other:segment:2" }),
    fragment("second", 2, { role: "user" }),
  ]) assert.equal(coalesceNarrativeFragments([first, second]).length, 2);
  assert.equal(coalesceNarrativeFragments([first, {id: "tool", kind: "execution"}, fragment("next", 2)]).length, 3);
});

test("attachment opening tool cards stay standalone in the message timeline", () => {
  const nodes = [
    {
      id: "reasoning",
      kind: "execution",
      executionType: "reasoning",
      content: "先看附件",
    },
    {
      id: "attachment-start",
      kind: "execution",
      executionType: "tool_call",
      toolCallId: "call_v8_attachment_preflight_abc123",
      toolName: "vision_media_analyzer",
    },
    {
      id: "narrative",
      kind: "narrative",
      content: "附件内容已读取。",
    },
  ];

  const segments = buildMessageTimelineSegments(nodes, { active: true });

  assert.equal(segments.length, 3);
  assert.equal(segments[0].kind, "trace_group");
  assert.equal(segments[1].kind, "node");
  assert.equal(segments[1].node.id, "attachment-start");
  assert.equal(segments[2].kind, "node");
});
