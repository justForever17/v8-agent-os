import assert from "node:assert/strict";
import test from "node:test";

import { buildMessageTimelineSegments } from "../dist/message-segments.js";

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
