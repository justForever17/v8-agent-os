import assert from "node:assert/strict";
import test from "node:test";
import { buildSessionExecutionGraph } from "../dist/runtime-episode-graph.js";
import { buildAuthoritativeRuntimeTimelineEntryFromEvent as fromEvent, normalizeAuthoritativeRuntimeTimeline as normalize } from "../dist/runtime-timeline.js";

test("partial handoffs and control receipts preserve active execution on live and replay", () => {
  const events = [
    { id: "1", topic: "runtime.episode.started", timestamp: 1, data: { episode: { episodeId: "A", kind: "research", state: "active" } } },
    { id: "2", topic: "handoff.ref.created", timestamp: 2, data: { handoff: { producerEpisodeId: "A", handoffRefId: "p1", status: "partial", executionTerminal: false, compactSummary: "part one" } } },
    { id: "3", topic: "runtime.episode.control.received", timestamp: 3, data: { episode: { episodeId: "A" }, control: { messageId: "cancel-1", kind: "cancel", deliveryState: "pending" } } },
    { id: "4", topic: "runtime.episode.control.applied", timestamp: 4, data: { episode: { episodeId: "A" }, control: { messageId: "steer-1", kind: "steer", deliveryState: "applied" } } },
  ];
  const graph = buildSessionExecutionGraph(events);
  const node = graph.nodes.find(item => item.id === "A");
  assert.equal(node.status, "active");
  assert.equal(node.controls["cancel-1"].deliveryState, "pending");
  assert.equal(node.controls["steer-1"].deliveryState, "applied");
  assert.equal(graph.nodes.find(item => item.id === "handoff:p1").status, "active");
  assert.deepEqual(buildSessionExecutionGraph(JSON.parse(JSON.stringify([...events].reverse()))), graph);
  const stopped = buildSessionExecutionGraph([...events,
    { id: "5", topic: "runtime.episode.cancelled", timestamp: 5, data: { episode: { episodeId: "A", state: "cancelled" } } },
    { id: "6", topic: "runtime.episode.control.stopped", timestamp: 6, data: { episode: { episodeId: "A" }, control: { messageId: "cancel-1", kind: "cancel", deliveryState: "stopped" } } },
    { ...events[2], id: "7", timestamp: 7 },
  ]).nodes.find(item => item.id === "A");
  assert.equal(stopped.status, "failed");
  assert.equal(stopped.controls["cancel-1"].deliveryState, "stopped");
});

test("timeline keeps each control and immutable partial version", () => {
  const entries = [1, 2].flatMap(seq => [
    fromEvent({ event_id: `c${seq}`, run_id: "run", seq, ts: "2026-09-13T00:00:00Z", topic: "runtime.episode.control.received",
      payload: { runtimeId: "research", episode: { episodeId: "A" }, control: { messageId: `control-${seq}`, kind: "steer", deliveryState: "pending" } } }),
    fromEvent({ event_id: `h${seq}`, run_id: "run", seq: seq + 2, ts: "2026-09-13T00:00:00Z", topic: "handoff.ref.created",
      payload: { runtimeId: "research", episode: { episodeId: "A" }, handoff: { handoffRefId: `partial-${seq}`, producerEpisodeId: "A", status: "partial" } } }),
  ]).filter(Boolean);
  assert.equal(entries.length, 4);
  assert.equal(normalize(entries).length, 4);
});
