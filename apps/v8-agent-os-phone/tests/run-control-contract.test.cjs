const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  deriveAuthoritativeRunControl,
  isActiveCommandSessionStatus,
  terminalRunStatusFromTopic,
} = require("@v8/session-realtime");

const phoneRoot = path.resolve(__dirname, "..");
const projectionSource = fs.readFileSync(
  path.join(phoneRoot, "src", "lib", "chat-projection.ts"),
  "utf8",
);
const chatScreenSource = fs.readFileSync(
  path.join(phoneRoot, "src", "screens", "ChatScreen.tsx"),
  "utf8",
);
const processSurfaceSources = [
  path.join(phoneRoot, "src", "components", "chat", "InteractiveTerminalCard.tsx"),
  path.join(phoneRoot, "src", "components", "chat", "ProcessesHUD.tsx"),
  path.join(phoneRoot, "src", "screens", "ChatScreen.tsx"),
].map((sourcePath) => fs.readFileSync(sourcePath, "utf8"));

test("Phone consumes the shared authoritative run-control contract", () => {
  assert.match(projectionSource, /deriveAuthoritativeRunControl\(\{/);
  assert.match(projectionSource, /pendingApproval: !isTerminal && hasPendingApproval/);
  assert.match(projectionSource, /canOpenApproval: !isTerminal/);
});

test("terminal state clears stale Phone interrupt affordances", () => {
  assert.deepEqual(deriveAuthoritativeRunControl({
    authoritativeStatus: "interrupted",
    optimisticStatus: "running",
    activeRunId: "run-phone",
    historicalRunId: "run-phone",
    hasActiveProcess: true,
    hasPendingApproval: true,
    controlCanInterrupt: true,
  }), {
    runId: "run-phone",
    status: "interrupted",
    canInterrupt: false,
    canRetry: false,
    canResume: false,
  });
});

test("authoritative active state clears a stale Phone idle projection", () => {
  assert.equal(deriveAuthoritativeRunControl({
    authoritativeStatus: "running",
    optimisticStatus: "idle",
    activeRunId: "run-phone-active",
    controlCanInterrupt: true,
  }).status, "running");
});

test("Phone applies terminal realtime and interrupt responses immediately", () => {
  assert.equal(terminalRunStatusFromTopic("run.interrupted"), "interrupted");
  assert.match(chatScreenSource, /const terminalRunStatus = terminalRunStatusFromTopic\(/);
  assert.match(chatScreenSource, /status: terminalRunStatus/);
  assert.match(chatScreenSource, /status: "interrupted"/);
  assert.match(chatScreenSource, /canInterrupt: false/);
});

test("Phone command surfaces stop polling every governed terminal state", () => {
  for (const status of ["timed_out", "interrupted", "cancelled", "failed", "completed"]) {
    assert.equal(isActiveCommandSessionStatus(status), false, status);
  }
  for (const source of processSurfaceSources) {
    assert.match(source, /isActiveCommandSessionStatus/);
  }
});
