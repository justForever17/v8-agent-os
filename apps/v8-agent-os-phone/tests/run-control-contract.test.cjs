const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  deriveAuthoritativeRunControl,
  buildClientToolSurface,
  isActiveRunStatus,
  isActiveCommandSessionStatus,
  isTerminalRunStatus,
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
const runtimeStageSource = fs.readFileSync(
  path.join(phoneRoot, "src", "lib", "runtime-stage.ts"),
  "utf8",
);
const contentDispatcherSource = fs.readFileSync(
  path.join(phoneRoot, "src", "components", "chat", "ContentDispatcher.tsx"),
  "utf8",
);
const toolCardSource = fs.readFileSync(
  path.join(phoneRoot, "src", "components", "chat", "ToolCard.tsx"),
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

test("Phone tool cards preserve authoritative command result status", () => {
  assert.equal(buildClientToolSurface({
    toolName: "run_system_command",
    state: "result",
    result: "$ pip install python-docx",
    resultStatus: "waiting",
  }).status, "waiting");
  assert.match(contentDispatcherSource, /resultStatus:\s*typeof resultStatus === "string"/);
  assert.match(toolCardSource, /toolInvocation\.clientSurface\?\.status/);
  assert.match(toolCardSource, /toolcard\.timed_out/);
  assert.match(toolCardSource, /toolcard\.terminated/);
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

test("Phone applies only the current run terminal realtime and interrupt responses immediately", () => {
  assert.equal(terminalRunStatusFromTopic("run.interrupted"), "interrupted");
  assert.match(chatScreenSource, /const terminalRunStatus = terminalRunStatusFromTopic\(/);
  assert.match(chatScreenSource, /const terminalTargetsCurrentRun = !terminalRunStatus[\s\S]*?pendingRunAcceptanceRef\.current/);
  assert.match(chatScreenSource, /if \(terminalRunStatus && terminalTargetsCurrentRun\)/);
  assert.match(chatScreenSource, /if \(terminalRunStatus && !terminalTargetsCurrentRun\) \{\s*return;\s*\}/);
  assert.match(chatScreenSource, /status: terminalRunStatus/);
  assert.match(chatScreenSource, /status: "interrupted"/);
  assert.match(chatScreenSource, /canInterrupt: false/);
  assert.match(chatScreenSource, /finally \{[\s\S]*?loadConversationRef\.current\(conversationId, \{ force: true \}\)/);
});

test("Phone rejects stale terminal events while a new run identity is awaiting acceptance", () => {
  assert.match(chatScreenSource, /pendingRunAcceptanceRef\.current = true;[\s\S]*?await submitChatMessage/);
  assert.match(chatScreenSource, /finally \{\s*pendingRunAcceptanceRef\.current = false;\s*setSending\(false\)/);
});

test("Phone composer and runtime stage share the complete active and terminal vocabulary", () => {
  assert.equal(isActiveRunStatus("waiting_external_tool"), true);
  assert.equal(isActiveRunStatus("interrupted"), false);
  assert.equal(isTerminalRunStatus("recoverable_failed"), true);
  assert.equal(isTerminalRunStatus("degraded"), true);
  assert.match(chatScreenSource, /isActiveRunStatus\(activeConversationStatus\)/);
  assert.match(chatScreenSource, /isTerminalRunStatus\(activeConversationStatus\)/);
  assert.match(runtimeStageSource, /const isBusy = isActiveRunStatus\(runtimeStatus\)/);
});

test("Phone command surfaces stop polling every governed terminal state", () => {
  for (const status of ["timed_out", "interrupted", "cancelled", "failed", "completed"]) {
    assert.equal(isActiveCommandSessionStatus(status), false, status);
  }
  for (const source of processSurfaceSources) {
    assert.match(source, /isActiveCommandSessionStatus/);
  }
});
