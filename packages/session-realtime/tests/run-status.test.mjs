import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveAuthoritativeRunActivity,
  shouldApplyRunScopedStatus,
  deriveAuthoritativeRunControl,
  isActiveRunStatus,
  isActiveCommandSessionStatus,
  isRecognizedRunStatus,
  isTerminalRunStatus,
  runStatusAllowsInterrupt,
  terminalRunStatusFromTopic,
} from "../dist/run-status.js";

test("authoritative terminal state settles stale local transport activity", () => {
  assert.equal(deriveAuthoritativeRunActivity({
    localStreamActive: true,
    localRunId: "run-terminal",
    runtimeStatus: "interrupted",
    runtimeRunId: "run-terminal",
    conversationStatus: "running",
    conversationRunId: "run-terminal",
  }), false);
});

test("local stream is only used before an authoritative state exists", () => {
  assert.equal(deriveAuthoritativeRunActivity({ localStreamActive: true }), true);
  assert.equal(deriveAuthoritativeRunActivity({ localStreamActive: false }), false);
});

test("a stale terminal snapshot from another run cannot settle the current stream", () => {
  assert.equal(deriveAuthoritativeRunActivity({
    localStreamActive: true,
    localRunId: "run-current",
    runtimeStatus: "completed",
    runtimeRunId: "run-previous",
  }), true);
  assert.equal(deriveAuthoritativeRunActivity({
    localStreamActive: true,
    runtimeStatus: "idle",
  }), true);
});

test("run-scoped status events cannot mutate a different current run", () => {
  assert.equal(shouldApplyRunScopedStatus("run-current", "run-current"), true);
  assert.equal(shouldApplyRunScopedStatus("run-previous", "run-current"), false);
  assert.equal(shouldApplyRunScopedStatus("", "run-current"), false);
  assert.equal(shouldApplyRunScopedStatus("run-first", ""), true);
  assert.equal(shouldApplyRunScopedStatus("run-previous", "", true), false);
  assert.equal(shouldApplyRunScopedStatus("run-current", "run-current", true), false);
});

test("shared status vocabulary governs interrupt and terminal topics", () => {
  assert.equal(runStatusAllowsInterrupt("queued"), true);
  assert.equal(runStatusAllowsInterrupt("running"), true);
  assert.equal(runStatusAllowsInterrupt("waiting_input"), true);
  assert.equal(runStatusAllowsInterrupt("completed"), false);
  assert.equal(isRecognizedRunStatus("interrupted"), true);
  assert.equal(terminalRunStatusFromTopic("run.interrupted"), "interrupted");
  assert.equal(terminalRunStatusFromTopic("run.controlled", { status: "interrupted" }), "interrupted");
  assert.equal(isRecognizedRunStatus("timed_out"), true);
  assert.equal(isActiveRunStatus("waiting_approval"), true);
  assert.equal(isActiveRunStatus("interrupted"), false);
  assert.equal(isTerminalRunStatus("recoverable_failed"), true);
  assert.equal(isTerminalRunStatus("degraded"), true);
  assert.equal(isTerminalRunStatus("unknown_backend_state"), false);
});

test("authoritative active state outranks a stale optimistic idle projection", () => {
  assert.deepEqual(deriveAuthoritativeRunControl({
    authoritativeStatus: "running",
    optimisticStatus: "idle",
    activeRunId: "run-active",
    historicalRunId: "run-active",
    controlCanInterrupt: true,
  }), {
    runId: "run-active",
    status: "running",
    canInterrupt: true,
    canRetry: false,
    canResume: false,
  });
});

test("command session terminal states never remain visually active", () => {
  for (const status of ["completed", "failed", "timed_out", "terminated", "stopped", "cancelled", "interrupted"]) {
    assert.equal(isActiveCommandSessionStatus(status), false, status);
  }
  for (const status of ["running", "awaiting_input", "render_stalled", "recoverable_stalled", ""]) {
    assert.equal(isActiveCommandSessionStatus(status), status !== "", status || "unknown");
  }
  assert.equal(isActiveCommandSessionStatus("unknown_backend_state"), false);
});

test("terminal run state outranks stale process and control projections", () => {
  assert.deepEqual(deriveAuthoritativeRunControl({
    authoritativeStatus: "interrupted",
    optimisticStatus: "running",
    activeRunId: "run-one",
    historicalRunId: "run-one",
    hasActiveProcess: true,
    hasPendingApproval: true,
    controlCanInterrupt: true,
  }), {
    runId: "run-one",
    status: "interrupted",
    canInterrupt: false,
    canRetry: false,
    canResume: false,
  });
});

test("terminal run state preserves history identity but exposes no active control", () => {
  assert.deepEqual(deriveAuthoritativeRunControl({
    authoritativeStatus: "failed",
    optimisticStatus: "running",
    historicalRunId: "run-failed",
    hasActiveProcess: true,
    controlCanInterrupt: true,
    controlCanRetry: true,
  }), {
    runId: "run-failed",
    status: "failed",
    canInterrupt: false,
    canRetry: true,
    canResume: false,
  });
});
