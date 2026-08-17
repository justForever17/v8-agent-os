import assert from "node:assert/strict";
import test from "node:test";

import {
  applyRealtimeEventToMessages,
  evaluateSessionRuntimeEvent,
  mergeTimelineNodesByIdentity,
  normalizeSessionRuntimeEvent,
  SessionRuntimeEventContiguousCursor,
  shouldAuthoritativelyRefreshOnRuntimeEvent,
} from "../dist/index.js";

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
