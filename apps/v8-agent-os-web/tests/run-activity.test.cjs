const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const { evaluateSessionRuntimeEvent, isActiveCommandSessionStatus } = require("@v8/session-realtime");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "src", "lib", "chat", "run-activity.ts");
const chatClientSource = fs.readFileSync(path.join(root, "src", "app", "chat", "ChatClient.tsx"), "utf8");
const streamHookSource = fs.readFileSync(path.join(root, "src", "hooks", "use-langgraph-stream.ts"), "utf8");
const commandSurfaceSources = [chatClientSource, ...[
  "InteractiveTerminalCard.tsx",
  "ManualTerminalPanel.tsx",
  "ProcessesHUD.tsx",
  "WorkspaceWorkbenchPanel.tsx",
].map((name) => fs.readFileSync(path.join(root, "src", "components", "chat", name), "utf8"))];
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  fileName: sourcePath,
}).outputText;
const testModule = { exports: {} };
new Function("require", "module", "exports", compiled)(require, testModule, testModule.exports);
const identityLedgerSourcePath = path.join(root, "src", "lib", "runtime-event-identity-ledger.ts");
const identityLedgerCompiled = ts.transpileModule(fs.readFileSync(identityLedgerSourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  fileName: identityLedgerSourcePath,
}).outputText;
const identityLedgerModule = { exports: {} };
new Function("require", "module", "exports", identityLedgerCompiled)(require, identityLedgerModule, identityLedgerModule.exports);
const { BoundedRuntimeEventIdentityLedger } = identityLedgerModule.exports;
const {
  deriveComposerRunActivity,
  deriveInterruptibleRunId,
  isRecognizedRunStatus,
  runStatusAllowsInterrupt,
  terminalRunStatusFromTopic,
} = testModule.exports;

test("authoritative terminal runtime status clears a stale running sidebar summary", () => {
  assert.equal(deriveComposerRunActivity({
    localStreamActive: true,
    localRunId: "run-terminal",
    runtimeStatus: "completed",
    runtimeRunId: "run-terminal",
    currentRunStatus: "completed",
    currentRunId: "run-terminal",
    conversationStatus: "running",
    conversationRunId: "run-terminal",
  }), false);
});

test("a stale terminal projection from another run cannot settle the submitted run", () => {
  assert.equal(deriveComposerRunActivity({
    localStreamActive: true,
    localRunId: "run-current",
    runtimeStatus: "completed",
    runtimeRunId: "run-previous",
  }), true);
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

test("terminal realtime events settle the matching durable run without aborting final buffered nodes", () => {
  assert.match(chatClientSource, /const settled = settleTerminalStream\(terminalRunId\)/);
  assert.match(streamHookSource, /const settleTerminalStream = useCallback\(\(runId\?: string \| null\) => \{/);
  assert.match(streamHookSource, /settleTerminalStream[\s\S]*?flushPendingMessages\(\);[\s\S]*?setIsLoading\(false\);/);
  const implementation = streamHookSource.match(/const settleTerminalStream = useCallback\(\(runId\?: string \| null\) => \{([\s\S]*?)\}, \[flushPendingMessages, setIsLoading\]\);/)?.[1] || "";
  assert.doesNotMatch(implementation, /\.abort\(/);
  assert.match(implementation, /submittedRunIdRef\.current !== normalizedRunId/);
});

test("only runtime statuses that Engine can interrupt expose a stop affordance", () => {
  assert.equal(runStatusAllowsInterrupt("running"), true);
  assert.equal(runStatusAllowsInterrupt("waiting_external_tool"), true);
  assert.equal(runStatusAllowsInterrupt("completed"), false);
  assert.equal(isRecognizedRunStatus("completed"), true);
  assert.equal(isRecognizedRunStatus("mystery"), false);
});

test("Web command surfaces share the complete command terminal vocabulary", () => {
  assert.equal(isActiveCommandSessionStatus("timed_out"), false);
  assert.equal(isActiveCommandSessionStatus("interrupted"), false);
  assert.equal(isActiveCommandSessionStatus("awaiting_input"), true);
  assert.equal(isActiveCommandSessionStatus("unknown"), false);
  for (const source of commandSurfaceSources) {
    assert.match(source, /isActiveCommandSessionStatus/);
  }
});

test("an external active run exposes the same stop target before compact controls hydrate", () => {
  assert.equal(deriveInterruptibleRunId({
    currentRunId: "run_external",
    currentRunStatus: "running",
    controlCanInterrupt: false,
  }), "run_external");
  assert.equal(deriveInterruptibleRunId({
    currentRunId: "run_external",
    currentRunStatus: "completed",
    controlCanInterrupt: false,
  }), null);
  assert.equal(deriveInterruptibleRunId({
    controlRunId: "run_controlled",
    controlCanInterrupt: true,
  }), "run_controlled");
});

test("Web reconciles remote runs and hydrates history when an initial snapshot has no messages", () => {
  assert.match(chatClientSource, /patchConversationSummary\(conversationId, \{ status: latestStatus \}\)/);
  assert.match(chatClientSource, /canStopRun=\{canInterruptProjectedRun\}/);
  assert.match(chatClientSource, /deriveInterruptibleRunId\(\{/);
  assert.match(chatClientSource, /snapshotHistoryFallbackRequested/);
  assert.match(chatClientSource, /Snapshot history hydration failed/);
});

test("Web starts chat work through the durable submit route and consumes progress from realtime", () => {
  assert.match(chatClientSource, /submitEndpoint: `\/api\/chat-submit`/);
  assert.match(streamHookSource, /const submitDurableRun = useCallback/);
  assert.match(streamHookSource, /submittedRunIdRef\.current = runId \|\| null/);
  assert.match(chatClientSource, /isLocalNdjsonStreamActive/);
  assert.match(chatClientSource, /streamingTransportRef\.current === "stream"/);
  assert.match(chatClientSource, /streamingConversationIdRef\.current = submittingConversationId/);
  assert.match(chatClientSource, /streamingTransportRef\.current = "submit"/);
});

test("Web accepts a durable realtime event before any event-driven side effect", () => {
  const start = chatClientSource.indexOf("const applyRemoteRuntimeEvent = useCallback");
  const end = chatClientSource.indexOf("\n    useEffect(() =>", start);
  const handler = chatClientSource.slice(start, end > start ? end : undefined);
  const acceptance = handler.indexOf("const acceptance = evaluateSessionRuntimeEvent");

  assert.ok(acceptance > 0);
  for (const sideEffect of [
    "ingestWorkbenchRuntimeEvent(rawEvent)",
    "const terminalRunStatus = terminalRunStatusFromTopic",
    "applyAskUserPendingApproval({",
    "upsertGovernanceApproval({",
    "void loadRuns(conversationId)",
  ]) {
    assert.ok(acceptance < handler.indexOf(sideEffect), `${sideEffect} must run after shared event acceptance`);
  }
});

test("Web prunes only realtime identities covered by an advancing snapshot", () => {
  assert.match(
    chatClientSource,
    /const snapshotWatermarkAdvanced = latestSeq > snapshotCoveredRealtimeSeqRef\.current;[\s\S]*?if \(snapshotWatermarkAdvanced\) \{\s*seenRealtimeEventIdentitiesRef\.current\.pruneSnapshotCovered\(latestSeq\);\s*\}/,
  );
  assert.match(
    chatClientSource,
    /if \(latestSeq > snapshotCoveredRealtimeSeqRef\.current\) \{\s*snapshotCoveredRealtimeSeqRef\.current = latestSeq;\s*seenRealtimeEventIdentitiesRef\.current\.pruneSnapshotCovered\(latestSeq\);\s*\}/,
  );
});

test("Web keeps a gap event identity beyond a partial snapshot watermark", () => {
  const ledger = new BoundedRuntimeEventIdentityLedger(8);
  const liveEvent = {
    type: "custom_event",
    topic: "subagent.tool.finished",
    seq: 15,
    event_id: "evt-web-15",
  };
  let sideEffects = 0;
  const first = evaluateSessionRuntimeEvent(liveEvent, {
    snapshotCoveredSeq: 10,
    contiguousSeq: 10,
    seenEventIdentities: ledger.seenIdentities,
  });
  assert.equal(first.accept, true);
  assert.deepEqual(first.gap, {
    expectedSeq: 11,
    observedSeq: 15,
    missingFromSeq: 11,
    missingToSeq: 14,
  });
  if (first.accept) {
    ledger.remember(first.identity, liveEvent.seq);
    sideEffects += 1;
  }

  ledger.pruneSnapshotCovered(12);
  const duplicate = evaluateSessionRuntimeEvent(liveEvent, {
    snapshotCoveredSeq: 12,
    seenEventIdentities: ledger.seenIdentities,
  });
  if (duplicate.accept) sideEffects += 1;

  assert.equal(duplicate.reason, "duplicate");
  assert.equal(sideEffects, 1);
  assert.equal(ledger.has(first.identity), true);
});

test("Web realtime identity retention stays bounded without guessing legacy snapshot coverage", () => {
  const ledger = new BoundedRuntimeEventIdentityLedger(2);
  ledger.remember("legacy-without-sequence", 0);
  ledger.pruneSnapshotCovered(99);
  assert.equal(ledger.has("legacy-without-sequence"), true);

  ledger.remember("event:100", 100);
  ledger.remember("event:101", 101);
  assert.equal(ledger.size, 2);
  assert.equal(ledger.has("legacy-without-sequence"), false);
  assert.equal(ledger.has("event:100"), true);
  assert.equal(ledger.has("event:101"), true);
});

test("Web scopes realtime sequence state to the active conversation", () => {
  assert.match(
    chatClientSource,
    /if \(previousConversationId === activeConversationId\) \{\s*return;\s*\}\s*latestRealtimeSeqRef\.current = 0;\s*snapshotCoveredRealtimeSeqRef\.current = 0;\s*seenRealtimeEventIdentitiesRef\.current\.clear\(\);/,
  );
});
