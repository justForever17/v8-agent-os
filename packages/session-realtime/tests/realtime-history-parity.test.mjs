import assert from "node:assert/strict";
import test from "node:test";

import {
  authoritativeSnapshotOmitsMessages,
  applyRealtimeEventToMessages,
  createInitialSessionRealtimeMessageState,
  evaluateSessionRuntimeEvent,
  isClientAudioAttachment,
  isClientVisualAttachment,
  mergeTimelineNodesByIdentity,
  normalizeSessionRuntimeEvent,
  queueSessionRealtimeRuntimeEvent,
  SessionRuntimeEventContiguousCursor,
  shouldAuthoritativelyRefreshOnRuntimeEvent,
} from "../dist/index.js";

test("compact authoritative snapshots distinguish omitted messages from an empty transcript", () => {
  const compact = {
    messagesOmitted: true,
    snapshot: { messages: [] },
  };
  const empty = {
    messagesOmitted: false,
    snapshot: { messages: [] },
  };

  assert.equal(authoritativeSnapshotOmitsMessages(compact), true);
  assert.equal(authoritativeSnapshotOmitsMessages(empty), false);
  assert.equal(authoritativeSnapshotOmitsMessages({ snapshot: { messagesOmitted: true } }), true);
});

test("queued snapshot deltas never coalesce across a tool boundary", () => {
  const state = createInitialSessionRealtimeMessageState();
  const firstReasoning = {
    type: "reasoning_chunk",
    topic: "run.reasoning.delta",
    run_id: "run-stream",
    node_id: "reasoning-node",
    seq: 1,
    data: { snapshot: "first" },
    targets: ["message"],
    visibility: "visible",
  };
  const toolEvent = {
    type: "tool_start",
    topic: "tool.started",
    run_id: "run-stream",
    node_id: "tool-node",
    seq: 2,
    tool: { toolCallId: "call-1", toolName: "read_native_file" },
    targets: ["message"],
    visibility: "visible",
  };
  const latestReasoning = {
    ...firstReasoning,
    seq: 3,
    data: { snapshot: "first second" },
  };

  assert.equal(queueSessionRealtimeRuntimeEvent(state, firstReasoning), true);
  assert.equal(queueSessionRealtimeRuntimeEvent(state, toolEvent), true);
  assert.equal(queueSessionRealtimeRuntimeEvent(state, latestReasoning), true);

  assert.deepEqual(
    state.pendingRuntimeEvents.map((event) => [event.type, event.seq]),
    [["reasoning_chunk", 1], ["tool_start", 2], ["reasoning_chunk", 3]],
  );
});

test("pure deltas without an authoritative snapshot are never coalesced", () => {
  const state = createInitialSessionRealtimeMessageState();
  const event = {
    type: "text_chunk",
    topic: "run.text.delta",
    run_id: "run-stream",
    node_id: "narrative-node",
    data: {},
    targets: ["message"],
    visibility: "visible",
  };

  queueSessionRealtimeRuntimeEvent(state, { ...event, seq: 1, content: "a" });
  queueSessionRealtimeRuntimeEvent(state, { ...event, seq: 2, content: "b" });

  assert.deepEqual(state.pendingRuntimeEvents.map((item) => item.seq), [1, 2]);
});

test("a field-sized reasoning snapshot burst keeps only the latest authoritative state", () => {
  const state = createInitialSessionRealtimeMessageState();

  for (let seq = 1; seq <= 2711; seq += 1) {
    assert.equal(queueSessionRealtimeRuntimeEvent(state, {
      type: "reasoning_chunk",
      topic: "run.reasoning.delta",
      run_id: "run-field-burst",
      node_id: "reasoning-node",
      seq,
      data: { snapshot: `reasoning-${seq}` },
      targets: ["message"],
      visibility: "visible",
    }), true);
  }

  assert.equal(state.pendingRuntimeEvents.length, 1);
  assert.equal(state.pendingRuntimeEvents[0]?.seq, 2711);
  assert.equal(state.pendingRuntimeEvents[0]?.data?.snapshot, "reasoning-2711");
});

test("snapshot watermark only rejects events already covered by the snapshot", () => {
  const seen = new Set();
  const lateButUncovered = evaluateSessionRuntimeEvent(
    { type: "custom_event", topic: "subagent.tool.finished", seq: 12, event_id: "evt-12" },
    { snapshotCoveredSeq: 10, seenEventIdentities: seen },
  );
  assert.equal(lateButUncovered.accept, true);
  assert.equal(lateButUncovered.gap, undefined);
  seen.add(lateButUncovered.identity);

  const outOfOrderUncovered = evaluateSessionRuntimeEvent(
    { type: "custom_event", topic: "subagent.text.delta", seq: 11, event_id: "evt-11" },
    { snapshotCoveredSeq: 10, seenEventIdentities: seen },
  );
  assert.equal(outOfOrderUncovered.accept, true);

  const replayedSnapshotEvent = evaluateSessionRuntimeEvent(
    { type: "custom_event", topic: "subagent.task.started", seq: 10, event_id: "evt-10" },
    { snapshotCoveredSeq: 10, seenEventIdentities: seen },
  );
  assert.deepEqual(replayedSnapshotEvent, {
    accept: false,
    identity: "event:evt-10",
    reason: "covered_by_snapshot",
  });

  const duplicate = evaluateSessionRuntimeEvent(
    { type: "custom_event", topic: "subagent.tool.finished", seq: 12, event_id: "evt-12" },
    { snapshotCoveredSeq: 10, seenEventIdentities: seen },
  );
  assert.equal(duplicate.reason, "duplicate");
});

test("sequence evaluation reports a recoverable gap without rejecting the event", () => {
  const acceptance = evaluateSessionRuntimeEvent(
    { type: "custom_event", topic: "runtime.episode.progress", seq: 14, event_id: "evt-14" },
    { snapshotCoveredSeq: 10, contiguousSeq: 10 },
  );

  assert.deepEqual(acceptance, {
    accept: true,
    identity: "event:evt-14",
    gap: {
      expectedSeq: 11,
      observedSeq: 14,
      missingFromSeq: 11,
      missingToSeq: 13,
    },
  });
});

test("contiguous cursor never advances polling beyond an observed gap", () => {
  const cursor = new SessionRuntimeEventContiguousCursor(10);

  assert.deepEqual(cursor.observe(13), {
    seq: 13,
    contiguousSeq: 10,
    highestObservedSeq: 13,
    acceptEvent: true,
    gap: {
      expectedSeq: 11,
      observedSeq: 13,
      missingFromSeq: 11,
      missingToSeq: 12,
    },
  });
  assert.equal(cursor.observe(11).contiguousSeq, 11);
  assert.equal(cursor.observe(12).contiguousSeq, 13);
  assert.equal(cursor.observe(13).contiguousSeq, 13);

  const snapshotRace = new SessionRuntimeEventContiguousCursor();
  snapshotRace.observe(12);
  assert.equal(snapshotRace.coverThrough(10).contiguousSeq, 10);
  assert.equal(snapshotRace.observe(11).contiguousSeq, 12);
});

test("contiguous cursor distinguishes pending duplicates from snapshot coverage", () => {
  const cursor = new SessionRuntimeEventContiguousCursor(10);

  assert.equal(cursor.observe(13).acceptEvent, true);
  assert.deepEqual(cursor.observe(13), {
    seq: 13,
    contiguousSeq: 10,
    highestObservedSeq: 13,
    acceptEvent: false,
    observationReason: "pending_duplicate",
    gap: {
      expectedSeq: 11,
      observedSeq: 13,
      missingFromSeq: 11,
      missingToSeq: 12,
    },
  });

  cursor.coverThrough(12);
  assert.equal(cursor.observe(13).observationReason, "contiguous_duplicate");
  cursor.coverThrough(13);
  assert.equal(cursor.observe(13).observationReason, "snapshot_covered");
});

test("timeline nodes with durable eventSeq render in canonical event order", () => {
  const nodes = mergeTimelineNodesByIdentity([], [
    { id: "completed", kind: "execution", eventSeq: 33, timestamp: 3000 },
    { id: "started", kind: "execution", eventSeq: 30, timestamp: 5000 },
    { id: "tool", kind: "execution", eventSeq: 32, timestamp: 1000 },
  ]);
  assert.deepEqual(nodes.map((node) => node.id), ["started", "tool", "completed"]);
});

test("recorded user voice is projected immediately without treating audio as an image", () => {
  const normalized = normalizeSessionRuntimeEvent({
    event_id: "evt-user-voice",
    run_id: "run-voice",
    seq: 17,
    ts: "2026-07-21T08:00:00.000Z",
    topic: "message.user.recorded",
    payload: {
      message_id: "desktop-pet-voice-1",
      clientMessageId: "desktop-pet-voice-1",
      content: "",
      images: ["/api/client/resource/voice.mp3"],
      attachments: [{
        id: "src-voice-1",
        sourceId: "src-voice-1",
        sourceKind: "desktop_pet_voice",
        resourceRole: "source",
        name: "voice.mp3",
        url: "/api/client/resource/voice.mp3",
        publicUrl: "/api/client/resource/voice.mp3",
        mimeType: "audio/mpeg",
        mediaKind: "audio",
      }],
    },
  });

  assert.ok(normalized);
  assert.equal(normalized.name, "message_user_recorded");
  assert.deepEqual(normalized.targets, ["message"]);
  assert.equal(shouldAuthoritativelyRefreshOnRuntimeEvent(normalized), true);

  const messages = [];
  const first = applyRealtimeEventToMessages(normalized, messages, undefined, {});
  assert.equal(first.currentAiMsg, undefined);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].role, "user");
  assert.deepEqual(messages[0].images, []);
  assert.equal(messages[0].metadata.attachments[0].mimeType, "audio/mpeg");
  assert.equal(messages[0].nodes[0].kind, "artifact");
  assert.equal(messages[0].nodes[0].artifact.resourceRole, "source");

  applyRealtimeEventToMessages(normalized, messages, undefined, {});
  assert.equal(messages.length, 1);
});

test("recorded WebM video remains visual when its media contract says video", () => {
  const normalized = normalizeSessionRuntimeEvent({
    event_id: "evt-user-video",
    run_id: "run-video",
    seq: 18,
    ts: "2026-07-21T08:01:00.000Z",
    topic: "message.user.recorded",
    payload: {
      message_id: "user-video-1",
      images: ["/api/client/resource/demo.webm"],
      attachments: [{
        id: "src-video-1",
        name: "demo.webm",
        url: "/api/client/resource/demo.webm",
        mimeType: "video/webm",
        mediaKind: "video",
      }],
    },
  });
  const messages = [];
  applyRealtimeEventToMessages(normalized, messages, undefined, {});
  assert.deepEqual(messages[0].images, ["/api/client/resource/demo.webm"]);
});

test("duplicate tool lifecycle events with a new node id keep one projected invocation", () => {
  const messages = [];
  const toolStart = (nodeId, seq) => ({
    type: "tool_start",
    run_id: "run-dedupe",
    node_id: nodeId,
    seq,
    ts: `2026-07-21T08:02:${String(seq).padStart(2, "0")}.000Z`,
    targets: ["message"],
    visibility: "visible",
    tool: {
      toolCallId: "call-dedupe",
      toolName: "write_native_file",
      args: { path: "demo.py" },
    },
  });

  applyRealtimeEventToMessages(toolStart("node-first", 19), messages, undefined, {});
  applyRealtimeEventToMessages(toolStart("node-replay", 20), messages, undefined, {});

  const toolCalls = (messages[0]?.nodes || []).filter((node) => node.executionType === "tool_call");
  assert.equal(toolCalls.length, 1);
  assert.equal(toolCalls[0].toolCallId, "call-dedupe");
});

test("attachment classification keeps ambiguous WebM voice and DOCX out of visual surfaces", () => {
  assert.equal(isClientVisualAttachment({ name: "voice.webm", mimeType: "audio/webm", mediaKind: "audio" }), false);
  assert.equal(isClientAudioAttachment({ name: "voice.webm", mimeType: "audio/webm", mediaKind: "audio" }), true);
  assert.equal(isClientVisualAttachment({ name: "legacy-voice.webm" }), false);
  assert.equal(isClientAudioAttachment({ name: "legacy-voice.webm" }), true);
  assert.equal(isClientVisualAttachment({ name: "report.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), false);
  assert.equal(isClientAudioAttachment({ name: "report.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), false);
  assert.equal(isClientVisualAttachment({ name: "clip.webm", mimeType: "video/webm", mediaKind: "video" }), true);
});

test("recorded DOCX stays a file attachment instead of becoming a broken image", () => {
  const normalized = normalizeSessionRuntimeEvent({
    event_id: "evt-user-docx",
    run_id: "run-docx",
    seq: 19,
    ts: "2026-08-19T08:01:00.000Z",
    topic: "message.user.recorded",
    payload: {
      message_id: "user-docx-1",
      images: ["/api/client/resource/report.docx"],
      attachments: [{
        id: "src-docx-1",
        name: "report.docx",
        url: "/api/client/resource/report.docx",
        mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        mediaKind: "document",
      }],
    },
  });
  const messages = [];

  applyRealtimeEventToMessages(normalized, messages, undefined, {});

  assert.deepEqual(messages[0].images, []);
  assert.equal(messages[0].metadata.attachments[0].name, "report.docx");
  assert.equal(messages[0].nodes[0].artifact.kind, "document");
});

test("reasoning deltas update one node with canonical millisecond timing", () => {
  const messages = [];
  const started = applyRealtimeEventToMessages({
    type: "reasoning_chunk",
    topic: "run.reasoning.delta",
    runtimeId: "chat",
    visibility: "visible",
    content: "first",
    data: { snapshot: "first", startTime: 1000, durationMs: 0 },
    run_id: "run-reasoning",
    node_id: "reasoning-1",
    ts: "1970-01-01T00:00:01.000Z",
    targets: ["message"],
  }, messages, undefined, {});

  const updated = applyRealtimeEventToMessages({
    type: "reasoning_chunk",
    topic: "run.reasoning.delta",
    runtimeId: "chat",
    visibility: "visible",
    content: " second",
    data: { snapshot: "first second", startTime: 1000, durationMs: 1750 },
    run_id: "run-reasoning",
    node_id: "reasoning-1",
    ts: "1970-01-01T00:00:02.750Z",
    targets: ["message"],
  }, messages, started.currentAiMsg, started.activeAgentProfile);

  assert.equal(updated.currentAiMsg.nodes.length, 1);
  assert.equal(updated.currentAiMsg.nodes[0].startTime, 1000);
  assert.equal(updated.currentAiMsg.nodes[0].time, 1750);
  assert.equal(updated.currentAiMsg.nodes[0].data.durationMs, 1750);
  assert.equal(updated.currentAiMsg.nodes[0].content, "first second");
});
