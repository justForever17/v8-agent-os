import assert from "node:assert/strict";

import { normalizeAuthoritativeRuntimeTimeline } from "../dist/runtime-timeline.js";

const baseTs = Date.parse("2026-06-04T12:00:00.000Z");

const duplicateFailures = normalizeAuthoritativeRuntimeTimeline([
  {
    id: "evt-failure-1",
    seq: 11,
    runtimeId: "engineering",
    topic: "runtime.episode.failed",
    summary: "工程 episode recoverable failure",
    status: "failed",
    timestamp: baseTs,
    data: {
      runId: "run_a",
      episodeId: "episode_a",
      status: "failed",
      reason: "runtime_episode_failed",
    },
  },
  {
    id: "evt-failure-2",
    seq: 12,
    runtimeId: "engineering",
    topic: "runtime.episode.failed",
    summary: "工程 episode recoverable failure · retry 2",
    status: "failed",
    timestamp: baseTs + 1000,
    data: {
      runId: "run_a",
      episodeId: "episode_a",
      status: "failed",
      reason: "runtime_episode_failed",
    },
  },
]);

assert.equal(duplicateFailures.length, 1, "duplicate runtime episode states should be coalesced");
assert.equal(duplicateFailures[0].id, "evt-failure-2", "latest duplicate event should replace older event");
assert.equal(duplicateFailures[0].seq, 12);
assert.ok(duplicateFailures[0].dedupeKey?.includes("episode_a"));

console.log("runtime timeline dedupe fixture verified");
