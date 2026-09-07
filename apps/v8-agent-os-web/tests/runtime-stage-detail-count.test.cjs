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
