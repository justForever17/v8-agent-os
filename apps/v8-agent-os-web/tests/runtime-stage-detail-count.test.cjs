const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function load(relative) {
  const file = path.resolve(__dirname, "../../..", relative);
  const code = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const result = { exports: {} };
  const dependencies = (name) => name === "@/src/lib/locale"
    ? { createTranslator: () => (key) => key } : require(name);
  new Function("require", "module", "exports", code)(dependencies, result, result.exports);
  return result.exports;
}

for (const [label, file, builder] of [
  ["Web", "apps/v8-agent-os-web/src/lib/runtime-stage.ts", "buildRuntimeStageModel"],
  ["Phone", "apps/v8-agent-os-phone/src/lib/runtime-stage.ts", "buildPhoneRuntimeStageModel"],
]) {
  const build = load(file)[builder];
  const entry = (seq, topic, state = "active") => ({
    id: `event-${seq}`, seq, timestamp: seq * 1000, runId: "run-live", runtimeId: "research", topic,
    kind: "progress", summary: `Operation ${seq}`, status: state,
    metadata: { episode: { episodeId: "episode-research", kind: "research", state },
      progress: { status: state, timelineNode: { id: `operation-${seq}`, topic: "research.progress.read" } } },
  });
  test(`${label} counts detail operations and keeps the owning episode active beside Supervisor`, () => {
    const timeline = [entry(1, "runtime.episode.started"), entry(2, "runtime.episode.progress"), entry(3, "runtime.episode.progress")];
    const model = build([], { runtimeTimeline: timeline, ownerRuntime: "chat", status: "running" });
    const card = model.items.find((item) => item.id === "research");
    assert.equal(card.eventCount, model.messageActivities.filter((item) => item.runtimeId === "research").length);
    assert.equal(card.eventCount, 3);
    assert.equal(card.status, "active");
    const complete = build([], { runtimeTimeline: [...timeline, entry(4, "runtime.episode.completed", "completed")], ownerRuntime: "chat", status: "running" });
    assert.equal(complete.items.find((item) => item.id === "research").status, "recent");
  });
}

const webStage = load("apps/v8-agent-os-web/src/lib/runtime-stage.ts");
const event = (seq, summary = `Operation ${seq}`) => ({
  id: `event-${seq}`, seq, timestamp: seq * 1000, runId: "run-window", runtimeId: "engineering",
  topic: "runtime.episode.completed", kind: "progress", summary, status: "completed",
});
const snapshot = (seqs, compacted, sessionId = "session-window", latestSeq = 175) => ({
  sessionId, latestSeq, runtimeTimeline: seqs.map((seq) => event(seq)),
  runtimeTimelineWindow: { compacted, sourceCount: 175, limit: compacted ? 160 : 175 },
});
const sequences = (value) => value.runtimeTimeline.map((entry) => entry.seq).sort((a, b) => a - b);

test("Web compact history cannot erase earlier live activities at the same snapshot watermark", () => {
  const current = snapshot([65, 66, 68, 70, 145, 146, 147], false);
  const incoming = snapshot([145, 146, 147], true);
  const merged = webStage.mergeRuntimeTimelineSnapshot(current, incoming);
  assert.deepEqual(sequences(merged), sequences(current));
  assert.equal(merged.latestSeq, 175);
  assert.deepEqual(merged.runtimeTimelineWindow, incoming.runtimeTimelineWindow);
  const model = webStage.buildRuntimeStageModel([], { runtimeTimeline: merged.runtimeTimeline, status: "completed" });
  assert.equal(model.items.find((item) => item.id === "engineering").eventCount, 7);
});

test("Web complete snapshot after partial window fills omitted history and applies authoritative deletion", () => {
  const partial = snapshot([146, 147], true);
  const complete = snapshot([65, 66, 68, 70, 145, 147], false, "session-window", 176);
  const merged = webStage.mergeRuntimeTimelineSnapshot(partial, complete);
  assert.deepEqual(sequences(merged), sequences(complete));
  assert.equal(merged.runtimeTimeline.some((item) => item.seq === 146), false);
  assert.equal(merged.runtimeTimelineWindow.compacted, false);
});

test("Web equal-watermark partial windows merge identities and preserve corrections", () => {
  const current = snapshot([65, 145], true);
  const incoming = snapshot([145, 147], true);
  incoming.runtimeTimeline[0] = event(145, "Corrected operation");
  const merged = webStage.mergeRuntimeTimelineSnapshot(current, incoming);
  assert.deepEqual(sequences(merged), [65, 145, 147]);
  assert.equal(merged.runtimeTimeline.find((item) => item.seq === 145).summary, "Corrected operation");
});

test("Web older compact snapshot neither regresses watermark nor overwrites newer item", () => {
  const current = snapshot([65, 145, 147], false, "session-window", 180);
  current.runtimeTimeline[1] = { ...event(145, "Current correction"), timestamp: 180000 };
  const merged = webStage.mergeRuntimeTimelineSnapshot(current, snapshot([145], true));
  assert.deepEqual(sequences(merged), [65, 145, 147]);
  assert.equal(merged.latestSeq, 180);
  assert.equal(merged.runtimeTimeline.find((item) => item.seq === 145).summary, "Current correction");
});

test("Web compact snapshots do not carry another session's history or watermark", () => {
  const incoming = snapshot([1, 2], true, "new-session", 2);
  const merged = webStage.mergeRuntimeTimelineSnapshot(snapshot([65, 145], false), incoming, true);
  assert.deepEqual(sequences(merged), [1, 2]);
  assert.equal(merged.latestSeq, 2);
  assert.equal(merged.sessionId, "new-session");
});

test("Web empty complete snapshot clears history while empty compact snapshot does not", () => {
  const current = snapshot([65, 145], false);
  assert.deepEqual(sequences(webStage.mergeRuntimeTimelineSnapshot(current, snapshot([], false))), []);
  assert.deepEqual(sequences(webStage.mergeRuntimeTimelineSnapshot(current, snapshot([], true))), [65, 145]);
  assert.deepEqual(sequences(webStage.mergeRuntimeTimelineSnapshot(null, snapshot([], true))), []);
});
