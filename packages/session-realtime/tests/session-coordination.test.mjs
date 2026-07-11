import assert from "node:assert/strict";
import test from "node:test";

import { normalizeSessionRuntimeEvent } from "../dist/event-normalizer.js";
import { applyRealtimeEventToMessages } from "../dist/message-lifecycle.js";
import { coerceAuthoritativeSessionSnapshot } from "../dist/cdc.js";


test("session coordination is session-scoped governance and remains non-user", () => {
  const event = normalizeSessionRuntimeEvent({
    event_id: "evt-coord-1",
    topic: "session_coordination.injected",
    seq: 12,
    session_id: "session-target-001",
    payload: {
      messageId: "coord-001",
      threadId: "thread-001",
      messageType: "request",
      sourceSessionId: "session-source-001",
      targetSessionId: "session-target-001",
      intent: "correct",
      authority: "current_user_explicit",
      state: "injected",
      summary: "Check the target session's latest instruction.",
      hopCount: 1,
      maxHops: 2,
      detailRef: "v8os-session-message:coord-001",
      direction: "incoming",
      message_id: "session_coordination:coord-001:incoming",
      node_id: "session_coordination:coord-001:incoming:governance",
      displayInMessage: true,
    },
  });
  assert.ok(event);
  assert.equal(event.name, "session_coordination");
  assert.equal(event.scope, "session");
  assert.deepEqual(event.targets, ["message", "runtime_card", "history"]);

  const result = applyRealtimeEventToMessages(event, [], undefined, {}, {
    createId: () => "generated-id",
  });
  assert.equal(result.currentAiMsg?.role, "assistant");
  const node = result.currentAiMsg?.nodes?.find((item) => item.kind === "governance");
  assert.equal(node?.governanceType, "session_coordination");
  assert.equal(node?.question, "Check the target session's latest instruction.");
  assert.equal(node?.requestInfo?.direction, "incoming");
});


test("later coordination transitions upsert the same governance node", () => {
  const basePayload = {
    messageId: "coord-002",
    threadId: "thread-002",
    messageType: "request",
    sourceSessionId: "session-source-001",
    targetSessionId: "session-target-001",
    intent: "request",
    authority: "ask_user_approved",
    summary: "Please report the current blocker.",
    hopCount: 1,
    maxHops: 2,
    detailRef: "v8os-session-message:coord-002",
    direction: "incoming",
    message_id: "session_coordination:coord-002:incoming",
    node_id: "session_coordination:coord-002:incoming:governance",
    displayInMessage: true,
  };
  const queued = normalizeSessionRuntimeEvent({
    event_id: "evt-coord-queued",
    topic: "session_coordination.queued",
    seq: 20,
    payload: { ...basePayload, state: "queued" },
  });
  const replied = normalizeSessionRuntimeEvent({
    event_id: "evt-coord-replied",
    topic: "session_coordination.replied",
    seq: 21,
    payload: { ...basePayload, state: "replied", replyStatus: "accepted" },
  });
  assert.ok(queued && replied);
  const first = applyRealtimeEventToMessages(queued, [], undefined, {}, { createId: () => "first" });
  const second = applyRealtimeEventToMessages(
    replied,
    first.currentAiMsg ? [first.currentAiMsg] : [],
    first.currentAiMsg,
    first.activeAgentProfile,
    { createId: () => "second" },
  );
  const nodes = second.currentAiMsg?.nodes?.filter((item) => item.kind === "governance") || [];
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].status, "replied");
  assert.equal(nodes[0].requestInfo?.replyStatus, "accepted");
});


test("authoritative snapshot retains compact coordination refs", () => {
  const snapshot = coerceAuthoritativeSessionSnapshot({
    sessionId: "session-target-001",
    sessionCoordinationMessages: [
      {
        messageId: "coord-003",
        threadId: "thread-003",
        messageType: "request",
        sourceSessionId: "session-source-001",
        targetSessionId: "session-target-001",
        intent: "inform",
        authority: "current_user_explicit",
        state: "queued",
        summary: "FYI",
        hopCount: 1,
        maxHops: 2,
        detailRef: "v8os-session-message:coord-003",
        createdAt: "2026-07-12T00:00:00Z",
        updatedAt: "2026-07-12T00:00:00Z",
      },
    ],
  });
  assert.equal(snapshot?.sessionCoordinationMessages?.[0]?.messageId, "coord-003");
});
