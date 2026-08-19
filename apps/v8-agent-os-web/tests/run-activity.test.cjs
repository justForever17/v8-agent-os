const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const { buildClientToolSurface, evaluateSessionRuntimeEvent, isActiveCommandSessionStatus } = require("@v8/session-realtime");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "src", "lib", "chat", "run-activity.ts");
const chatClientSource = fs.readFileSync(path.join(root, "src", "app", "chat", "ChatClient.tsx"), "utf8");
const streamHookSource = fs.readFileSync(path.join(root, "src", "hooks", "use-langgraph-stream.ts"), "utf8");
const runtimeStageSource = fs.readFileSync(path.join(root, "src", "lib", "runtime-stage.ts"), "utf8");
const contentDispatcherSource = fs.readFileSync(path.join(root, "src", "components", "chat", "ContentDispatcher.tsx"), "utf8");
const toolCardSource = fs.readFileSync(path.join(root, "src", "components", "chat", "ToolCard.tsx"), "utf8");
const chatMessageSource = fs.readFileSync(path.join(root, "src", "components", "chat", "ChatMessage.tsx"), "utf8");
const timelineGrouperSourcePath = path.join(root, "src", "lib", "chat", "timeline-grouper.ts");
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
const timelineGrouperCompiled = ts.transpileModule(fs.readFileSync(timelineGrouperSourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  fileName: timelineGrouperSourcePath,
}).outputText;
const timelineGrouperModule = { exports: {} };
new Function("require", "module", "exports", timelineGrouperCompiled)(require, timelineGrouperModule, timelineGrouperModule.exports);
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
  deriveMatchingTerminalProjection,
  shouldSettleSubmittedRun,
  shouldPreserveCurrentHistoryOnEmpty,
  isActiveRunStatus,
  isRecognizedRunStatus,
  isTerminalRunStatus,
  runStatusAllowsInterrupt,
  shouldApplyRunScopedStatus,
  terminalRunStatusFromTopic,
} = testModule.exports;

test("a matching terminal snapshot settles the submitted run", () => {
  assert.deepEqual(deriveMatchingTerminalProjection({
    localRunId: "run-current",
    runtimeStatus: "completed",
    projectedRunId: "run-current",
    currentRunStatus: "completed",
    currentRunId: "run-current",
  }), {
    runId: "run-current",
    status: "completed",
  });
});

test("a stale or not-yet-accepted snapshot cannot settle the submitted run", () => {
  assert.equal(deriveMatchingTerminalProjection({
    localRunId: "run-current",
    runtimeStatus: "completed",
    projectedRunId: "run-previous",
  }), null);
  assert.equal(deriveMatchingTerminalProjection({
    localRunId: "run-current",
    acceptancePending: true,
    runtimeStatus: "completed",
    projectedRunId: "run-current",
  }), null);
});

test("an authoritative active status outranks a stale nested terminal record", () => {
  assert.equal(deriveMatchingTerminalProjection({
    localRunId: "run-current",
    runtimeStatus: "running",
    projectedRunId: "run-current",
    currentRunStatus: "completed",
    currentRunId: "run-current",
  }), null);
});

test("terminal settlement is idempotent and run scoped", () => {
  assert.equal(shouldSettleSubmittedRun({
    submittedRunId: "run-current",
    terminalRunId: "run-current",
  }), true);
  assert.equal(shouldSettleSubmittedRun({
    submittedRunId: "",
    terminalRunId: "run-current",
  }), false);
  assert.equal(shouldSettleSubmittedRun({
    submittedRunId: "run-current",
    terminalRunId: "",
  }), false);
  assert.equal(shouldSettleSubmittedRun({
    submittedRunId: "run-current",
    terminalRunId: "run-previous",
  }), false);
  assert.equal(shouldSettleSubmittedRun({
    submittedRunId: "run-current",
    terminalRunId: "run-current",
    acceptancePending: true,
  }), false);
});

test("terminal reconciliation cannot replace an authoritative snapshot with an empty turn page", () => {
  assert.equal(shouldPreserveCurrentHistoryOnEmpty({
    preserveCurrentOnEmpty: true,
    currentMessageCount: 3,
    incomingMessageCount: 0,
  }), true);
  assert.equal(shouldPreserveCurrentHistoryOnEmpty({
    preserveCurrentOnEmpty: false,
    currentMessageCount: 3,
    incomingMessageCount: 0,
  }), false);
  assert.equal(shouldPreserveCurrentHistoryOnEmpty({
    preserveCurrentOnEmpty: true,
    currentMessageCount: 3,
    incomingMessageCount: 2,
  }), false);
});

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

test("Web tool cards render authoritative non-success statuses instead of treating every result as completed", () => {
  assert.equal(buildClientToolSurface({
    toolName: "run_system_command",
    state: "result",
    result: "$ pip install python-docx",
    resultStatus: "waiting",
  }).status, "waiting");
  assert.match(contentDispatcherSource, /resultStatus:\s*typeof resultStatus === 'string'/);
  assert.match(toolCardSource, /toolInvocation\.clientSurface\?\.status/);
  assert.match(toolCardSource, /web\.toolCard\.timedOut/);
  assert.match(toolCardSource, /web\.toolCard\.terminated/);
});

test("completed traces with file-producing tools are visible without a second click", () => {
  assert.match(chatMessageSource, /hasArtifactProducingTool\(segment\)/);
  assert.match(chatMessageSource, /toolName === "write_native_file"/);
  assert.match(chatMessageSource, /const defaultExpanded = hasArtifactProducingTool\(segment\)/);
});

test("a stale terminal projection from another run cannot settle the submitted run", () => {
  assert.equal(deriveComposerRunActivity({
    localStreamActive: true,
    localRunId: "run-current",
    runtimeStatus: "completed",
    runtimeRunId: "run-previous",
  }), true);
});

test("Web rejects a delayed terminal event from a previous run", () => {
  assert.equal(shouldApplyRunScopedStatus("run-previous", "run-current"), false);
  assert.equal(shouldApplyRunScopedStatus("run-current", "run-current"), true);
  assert.equal(shouldApplyRunScopedStatus("run-previous", "", true), false);
  assert.match(chatClientSource, /isRunAcceptancePending\(\)/);
  assert.match(chatClientSource, /getSubmittedRunId\(\) \|\| localSubmittedRunId \|\| projectionRunId/);
  assert.match(chatClientSource, /if \(terminalTargetsCurrentRun\) \{[\s\S]*?patchConversationSummary\(conversationId, \{ status: terminalRunStatus \}\)/);
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
  assert.match(implementation, /shouldSettleSubmittedRun\(\{/);
  assert.match(implementation, /durableSubmitPendingRef\.current/);
});

test("terminal snapshots settle the matching durable run and replace optimistic deltas", () => {
  assert.match(chatClientSource, /deriveMatchingTerminalProjection\(\{/);
  assert.match(chatClientSource, /const terminalStreamSettled = terminalProjection[\s\S]*?settleTerminalStream\(terminalProjection\.runId\)/);
  assert.match(chatClientSource, /mergeWithCurrent: localStreamActive && !terminalStreamSettled/);
  assert.match(chatClientSource, /readString\(terminalEventData\.run_id\)[\s\S]*?readString\(terminalEventData\.runId\)/);
});

test("Web keeps a new durable submission busy until its run identity is accepted", () => {
  assert.match(streamHookSource, /durableSubmitPendingRef\.current = true;[\s\S]*?await submitDurableRun/);
  assert.match(streamHookSource, /submittedRunIdRef\.current = queued \? null : runId;[\s\S]*?durableSubmitPendingRef\.current = false/);
  assert.match(chatClientSource, /!isRunAcceptancePendingRef\.current\(\)/);
});

test("only runtime statuses that Engine can interrupt expose a stop affordance", () => {
  assert.equal(runStatusAllowsInterrupt("running"), true);
  assert.equal(runStatusAllowsInterrupt("waiting_external_tool"), true);
  assert.equal(runStatusAllowsInterrupt("completed"), false);
  assert.equal(isRecognizedRunStatus("completed"), true);
  assert.equal(isRecognizedRunStatus("mystery"), false);
});

test("all Web busy surfaces consume the shared terminal vocabulary", () => {
  assert.equal(isActiveRunStatus("waiting_approval"), true);
  assert.equal(isActiveRunStatus("interrupted"), false);
  assert.equal(isTerminalRunStatus("recoverable_failed"), true);
  assert.equal(isTerminalRunStatus("degraded"), true);
  assert.match(runtimeStageSource, /const isBusy = isActiveRunStatus\(runtimeStatus\)/);
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
  assert.match(chatClientSource, /isTerminalRunStatus\(latestStatus\)[\s\S]*?settleTerminalStreamRef\.current\(latestRun\.id\)/);
  assert.match(chatClientSource, /\.finally\(\(\) => \{[\s\S]*?void loadRuns\(conversationId\)/);
  assert.match(streamHookSource, /dispatchRunCommand[\s\S]*?signal: AbortSignal\.timeout\(10_000\)/);
  assert.match(chatClientSource, /canStopRun=\{canInterruptProjectedRun\}/);
  assert.match(chatClientSource, /deriveInterruptibleRunId\(\{/);
  assert.match(chatClientSource, /requestAuthoritativeResync\("snapshot_without_messages"\)/);
  assert.match(chatClientSource, /requestAuthoritativeResync\("sse_error"\)/);
  assert.match(chatClientSource, /Promise\.allSettled\(\[/);
  assert.doesNotMatch(chatClientSource, /if \(!isLocalStreamActive\(activeConversationId\)\) \{\s*requestAuthoritativeResync/);
});

test("Web replaces live deltas with canonical history when a run reaches terminal state", () => {
  assert.match(
    chatClientSource,
    /const localStreamWasActive = terminalTargetsCurrentRun && isLocalStreamActive\(conversationId\);[\s\S]*?canonicalReloadDeferredToStreamCompletion = true/,
  );
  assert.match(
    chatClientSource,
    /if \(!canonicalReloadDeferredToStreamCompletion\) \{[\s\S]*?mergeWithCurrent: false,[\s\S]*?preserveCurrentOnEmpty: true/,
  );
  assert.match(
    chatClientSource,
    /if \(!wasLoading \|\| isLoading \|\| !conversationId\) \{\s*return;\s*\}[\s\S]*?preserveCurrentOnEmpty: true/,
  );
  assert.match(chatClientSource, /preserveCurrentHistory \? messagesRef\.current : normalized/);
});

test("conversation identity hydration does not rerun merely because local loading settles", () => {
  const effectStart = chatClientSource.indexOf("// Fetch history when ID changes");
  const effectEnd = chatClientSource.indexOf("const eventSource = new EventSource", effectStart);
  const effect = chatClientSource.slice(effectStart, effectEnd);

  assert.ok(effectStart > 0 && effectEnd > effectStart);
  assert.match(effect, /const localStreamLoading = isLoadingRef\.current/);
  assert.doesNotMatch(effect, /\[activeConversationId,[^\]]*\bisLoading\b/);
});

test("Web timeline counts command tools and treats canonical node time as milliseconds", () => {
  const grouper = fs.readFileSync(timelineGrouperSourcePath, "utf8");
  assert.doesNotMatch(grouper, /toolName === "start_background_command" \|\| toolName === "run_system_command"/);
  assert.match(grouper, /return nodeTime \/ 1000/);
  assert.match(grouper, /totalDuration \+= extractNodeDuration\(execNode\)/);
});

test("Web tool totals deduplicate replayed identities without merging distinct runs", () => {
  const { groupTimelineNodes } = timelineGrouperModule.exports;
  const nodes = [
    { id: "node-1", kind: "execution", executionType: "tool_call", toolCallId: "call-replayed", runId: "run-a", ownerStreamKey: "supervisor", timestamp: 1 },
    { id: "node-2", kind: "execution", executionType: "tool_call", toolCallId: "call-replayed", runId: "run-a", ownerStreamKey: "supervisor", timestamp: 2 },
    { id: "node-3", kind: "execution", executionType: "tool_call", toolCallId: "call-replayed", runId: "run-b", ownerStreamKey: "supervisor", timestamp: 3 },
    { id: "node-4", kind: "execution", executionType: "tool_call", toolName: "run_system_command", runId: "run-b", ownerStreamKey: "supervisor", timestamp: 4 },
  ];
  const [segment] = groupTimelineNodes(nodes, new Map());
  assert.equal(segment.toolCount, 3);
});

test("Web starts chat work through the durable submit route and consumes progress from realtime", () => {
  assert.match(chatClientSource, /submitEndpoint: `\/api\/chat-submit`/);
  assert.match(streamHookSource, /const submitDurableRun = useCallback/);
  assert.match(streamHookSource, /submittedRunIdRef\.current = queued \? null : runId/);
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
  assert.match(chatClientSource, /snapshotCoveredRealtimeSeqRef\.current = Math\.max\(snapshotCoveredRealtimeSeqRef\.current, latestSeq\)/);
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
