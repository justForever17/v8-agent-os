const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const sourcePath = path.join(__dirname, "..", "src", "lib", "server", "runtime-event-delivery.ts");
const eventSequenceSourcePath = path.join(
  __dirname,
  "..",
  "..",
  "..",
  "packages",
  "session-realtime",
  "src",
  "event-sequence.ts",
);

function loadTypeScriptModule(modulePath) {
  const output = ts.transpileModule(fs.readFileSync(modulePath, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: modulePath,
  }).outputText;
  const moduleRecord = { exports: {} };
  new Function("require", "module", "exports", output)(require, moduleRecord, moduleRecord.exports);
  return moduleRecord.exports;
}

const {
    buildRuntimeEventDedupeKey,
    buildRuntimeEventDeliveryIdentity,
    RuntimeEventGapRecoveryThrottle,
    shouldRequestSnapshotForEmptyEventPage,
    shouldDeliverRuntimeEventObservation,
} = loadTypeScriptModule(sourcePath);
const { SessionRuntimeEventContiguousCursor } = loadTypeScriptModule(eventSequenceSourcePath);

test("durable event ids take precedence over a shared fallback dedupe key", () => {
  assert.equal(buildRuntimeEventDeliveryIdentity({
    eventId: "event-progress-a",
    dedupeKey: "coarse-progress-key",
    topic: "runtime.episode.progress",
  }), "event:event-progress-a");
  assert.equal(buildRuntimeEventDeliveryIdentity({
    eventId: "event-progress-b",
    dedupeKey: "coarse-progress-key",
    topic: "runtime.episode.progress",
  }), "event:event-progress-b");
});

test("progress fallback keeps distinct timeline, tool, and segment identities", () => {
  const envelope = (progress) => ({
    record: { topic: "runtime.episode.progress", run_id: "run-a" },
    data: {
      episode: { episodeId: "delegation-a" },
      progress,
    },
    payload: {},
  });
  const reasoning = envelope({ stage: "reasoning", timelineNode: { id: "message-a:reasoning" } });
  const tool = envelope({ stage: "tool_execution", timelineNode: { toolCallId: "tool-a" } });
  const segment = envelope({ stage: "responding", segmentId: "segment-a" });

  const keys = [reasoning, tool, segment].map(({ record, data, payload }) => (
    buildRuntimeEventDedupeKey(record, data, payload)
  ));
  assert.equal(new Set(keys).size, 3);
  assert.ok(keys.every(Boolean));
});

test("progress without a stable identity is not collapsed into a status bucket", () => {
  const key = buildRuntimeEventDedupeKey(
    { topic: "runtime.episode.progress", run_id: "run-a", status: "running" },
    { episode: { episodeId: "delegation-a" }, progress: { stage: "working", status: "running" } },
    {},
  );
  assert.equal(key, "");
  assert.equal(buildRuntimeEventDeliveryIdentity({ dedupeKey: key, topic: "runtime.episode.progress" }), "");
});

test("gap recovery requests an immediate snapshot, throttles retries, and resets after coverage", () => {
  const recovery = new RuntimeEventGapRecoveryThrottle(2_000);
  const gap = {
    expectedSeq: 11,
    observedSeq: 13,
    missingFromSeq: 11,
    missingToSeq: 12,
  };

  assert.equal(recovery.shouldRequestSnapshot(gap, 10_000), true);
  assert.equal(recovery.shouldRequestSnapshot(gap, 11_999), false);
  assert.equal(recovery.shouldRequestSnapshot(gap, 12_000), true);
  assert.equal(recovery.shouldRequestSnapshot(undefined, 12_100), false);
  assert.equal(recovery.shouldRequestSnapshot(gap, 12_101), true);
});

test("a pruned sequence gap keeps snapshot recovery alive until an authoritative watermark covers it", () => {
  const cursor = new SessionRuntimeEventContiguousCursor(10);
  const recovery = new RuntimeEventGapRecoveryThrottle(2_000);

  const firstGap = cursor.observe(13);
  assert.equal(firstGap.contiguousSeq, 10);
  assert.equal(recovery.shouldRequestSnapshot(firstGap.gap, 20_000), true);

  const stillMissing = cursor.observe(12);
  assert.equal(stillMissing.contiguousSeq, 10);
  assert.equal(recovery.shouldRequestSnapshot(stillMissing.gap, 21_000), false);
  assert.equal(recovery.shouldRequestSnapshot(stillMissing.gap, 22_000), true);

  const covered = cursor.coverThrough(13);
  assert.equal(covered.contiguousSeq, 13);
  assert.equal(covered.gap, undefined);
  assert.equal(recovery.shouldRequestSnapshot(covered.gap, 22_100), false);
  assert.equal(recovery.shouldRequestSnapshot(cursor.observe(15).gap, 22_101), true);
});

test("snapshot coverage rejects a delayed polling page after the snapshot wins the race", () => {
  const cursor = new SessionRuntimeEventContiguousCursor();

  cursor.coverThrough(600);
  const delayedPoll = cursor.observe(1);

  assert.equal(delayedPoll.observationReason, "snapshot_covered");
  assert.equal(shouldDeliverRuntimeEventObservation(delayedPoll), false);
});

test("an empty replay page with a newer watermark requests snapshot recovery", () => {
  assert.equal(shouldRequestSnapshotForEmptyEventPage(3167, 3160), true);
  assert.equal(shouldRequestSnapshotForEmptyEventPage(3160, 3160), false);
  assert.equal(shouldRequestSnapshotForEmptyEventPage(0, 3160), false);
});

test("more than 512 pending events are delivered once while a gap awaits snapshot recovery", () => {
  const cursor = new SessionRuntimeEventContiguousCursor(10);
  const observations = [];

  for (let seq = 13; seq <= 700; seq += 1) {
    observations.push(cursor.observe(seq));
  }
  const replayed = [];
  for (let seq = 13; seq <= 700; seq += 1) {
    replayed.push(cursor.observe(seq));
  }

  assert.equal(observations.filter(shouldDeliverRuntimeEventObservation).length, 688);
  assert.equal(replayed.filter(shouldDeliverRuntimeEventObservation).length, 0);
  assert.ok(replayed.every((item) => item.observationReason === "pending_duplicate"));
  assert.equal(cursor.contiguousSeq, 10);
});
