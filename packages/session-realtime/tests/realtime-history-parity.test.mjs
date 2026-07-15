import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateSessionRuntimeEvent,
  mergeTimelineNodesByIdentity,
} from "../dist/index.js";

test("snapshot watermark only rejects events already covered by the snapshot", () => {
  const seen = new Set();
  const lateButUncovered = evaluateSessionRuntimeEvent(
    { type: "custom_event", topic: "subagent.tool.finished", seq: 12, event_id: "evt-12" },
    { snapshotCoveredSeq: 10, seenEventIdentities: seen },
  );
  assert.equal(lateButUncovered.accept, true);
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

test("timeline nodes with durable eventSeq render in canonical event order", () => {
  const nodes = mergeTimelineNodesByIdentity([], [
    { id: "completed", kind: "execution", eventSeq: 33, timestamp: 3000 },
    { id: "started", kind: "execution", eventSeq: 30, timestamp: 5000 },
    { id: "tool", kind: "execution", eventSeq: 32, timestamp: 1000 },
  ]);
  assert.deepEqual(nodes.map((node) => node.id), ["started", "tool", "completed"]);
});
