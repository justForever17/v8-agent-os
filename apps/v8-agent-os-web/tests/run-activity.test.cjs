const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "src", "lib", "chat", "run-activity.ts");
const chatClientSource = fs.readFileSync(path.join(root, "src", "app", "chat", "ChatClient.tsx"), "utf8");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  fileName: sourcePath,
}).outputText;
const testModule = { exports: {} };
new Function("require", "module", "exports", compiled)(require, testModule, testModule.exports);
const {
  deriveComposerRunActivity,
  isRecognizedRunStatus,
  runStatusAllowsInterrupt,
  terminalRunStatusFromTopic,
} = testModule.exports;

test("authoritative terminal runtime status clears a stale running sidebar summary", () => {
  assert.equal(deriveComposerRunActivity({
    localStreamActive: false,
    runtimeStatus: "completed",
    currentRunStatus: "completed",
    conversationStatus: "running",
  }), false);
});

test("external active run keeps the composer busy without a local HTTP stream", () => {
  assert.equal(deriveComposerRunActivity({
    localStreamActive: false,
    runtimeStatus: "running",
    conversationStatus: "idle",
  }), true);
});

test("sidebar status is only used before an authoritative projection exists", () => {
  assert.equal(deriveComposerRunActivity({
    localStreamActive: false,
    conversationStatus: "running",
  }), true);
});

test("terminal topics provide an immediate status before the next snapshot", () => {
  assert.equal(terminalRunStatusFromTopic("run.completed"), "completed");
  assert.equal(terminalRunStatusFromTopic("run.state.changed", { status: "failed" }), "failed");
  assert.equal(terminalRunStatusFromTopic("tool.finished", { status: "completed" }), null);
});

test("only runtime statuses that Engine can interrupt expose a stop affordance", () => {
  assert.equal(runStatusAllowsInterrupt("running"), true);
  assert.equal(runStatusAllowsInterrupt("waiting_external_tool"), true);
  assert.equal(runStatusAllowsInterrupt("completed"), false);
  assert.equal(isRecognizedRunStatus("completed"), true);
  assert.equal(isRecognizedRunStatus("mystery"), false);
});

test("Web reconciles remote runs and hydrates history when an initial snapshot has no messages", () => {
  assert.match(chatClientSource, /patchConversationSummary\(conversationId, \{ status: latestStatus \}\)/);
  assert.match(chatClientSource, /canStopRun=\{isLoading \|\| canInterruptProjectedRun\}/);
  assert.match(chatClientSource, /snapshotHistoryFallbackRequested/);
  assert.match(chatClientSource, /Snapshot history hydration failed/);
});
