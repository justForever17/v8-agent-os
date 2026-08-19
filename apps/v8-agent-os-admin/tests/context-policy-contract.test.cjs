const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");
const panel = fs.readFileSync(
  path.join(repoRoot, "apps/v8-agent-os-admin/src/components/memory/MemoryContextPanel.tsx"),
  "utf8",
);

test("context policy removes retired LangGraph graph-budget keys before save", () => {
  assert.match(panel, /for \(const legacyKey of \[/);
  assert.match(panel, /"recursion_limit"/);
  assert.match(panel, /"recursionLimit"/);
  assert.match(panel, /"maxGraphContinuations"/);
  assert.match(panel, /"max_graph_continuations"/);
  assert.match(panel, /delete policyWithoutGraphBudget\[legacyKey\]/);
  assert.match(panel, /\.\.\.policyWithoutGraphBudget/);
  assert.doesNotMatch(panel, /policyForm\.recursion_limit/);
  assert.doesNotMatch(panel, /policyForm\.maxGraphContinuations/);
});
