import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAuthoritativeRuntimeTimelineEntryFromEvent as fromEvent,
  normalizeAuthoritativeRuntimeTimeline as normalize,
} from "../dist/runtime-timeline.js";

function progress(seq, { runtimeId = "research", episodeId = "episode-a", nodeId = "read-a", status = "completed" } = {}) {
  return {
    event_id: `event-${seq}`,
    run_id: "run-a",
    seq,
    ts: "2026-09-05T10:00:00Z",
    topic: "runtime.episode.progress",
    payload: {
      runtimeId,
      episode: { episodeId, runtimeKind: runtimeId },
      status,
      summary: `operation-${nodeId}`,
      progress: {
        status,
        summary: `operation-${nodeId}`,
        timelineNode: { id: nodeId, kind: "execution", topic: `${runtimeId}.progress.read` },
      },
    },
  };
}

for (const runtimeId of ["research", "computer_use", "rpa", "subagent_swarm"]) {
  test(`${runtimeId} live operations use the same identity as the Engine snapshot`, () => {
    const events = [
      progress(1, { runtimeId, status: "running" }),
      progress(2, { runtimeId, nodeId: "read-b" }),
      progress(3, { runtimeId }),
      progress(4, { runtimeId, episodeId: "episode-b" }),
    ];
    const live = events.map((event) => fromEvent(event));
    const history = live.map((entry, index) => {
      const { episode, progress: step } = events[index].payload;
      const key = runtimeId === "subagent_swarm"
        ? `subagent-timeline:${episode.episodeId}:${step.timelineNode.id}`
        : `runtime-timeline:${runtimeId}:${episode.episodeId}:${step.timelineNode.id}`;
      return { ...entry, dedupeKey: key, metadata: { ...entry.metadata, dedupeKey: key } };
    });
    assert.deepEqual(normalize(live).map((entry) => entry.seq), [4, 3, 2]);
    assert.deepEqual(normalize(live).map((entry) => entry.dedupeKey), normalize(history).map((entry) => entry.dedupeKey));
    assert.deepEqual(normalize([...live, ...history, ...live]).map((entry) => entry.seq), [4, 3, 2]);
    assert.deepEqual(normalize([...history, ...live.toReversed()]).map((entry) => entry.seq), [4, 3, 2]);
  });
}

test("scheduler-only progress and foreign runtime nodes stay off the detail timeline", () => {
  const scheduler = progress(1);
  delete scheduler.payload.progress.timelineNode;
  assert.equal(fromEvent(scheduler), null);
  const foreign = progress(2);
  foreign.payload.progress.timelineNode.topic = "computer_use.progress.click";
  assert.equal(fromEvent(foreign), null);
});

test("a valid node without an id retains its durable event identity", () => {
  const events = [progress(1), progress(2)];
  for (const event of events) delete event.payload.progress.timelineNode.id;
  assert.deepEqual(normalize(events.map((event) => fromEvent(event))).map((entry) => entry.seq), [2, 1]);
});

test("an explicit authoritative operation key is preserved", () => {
  const event = progress(1);
  event.payload.dedupeKey = "canonical-operation-key";
  assert.equal(fromEvent(event).dedupeKey, "canonical-operation-key");
});
