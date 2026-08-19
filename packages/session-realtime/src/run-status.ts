export const ACTIVE_RUN_STATUSES: ReadonlySet<string> = new Set([
  "running",
  "queued",
  "pending",
  "starting",
  "streaming",
  "waiting_input",
  "waiting_approval",
  "waiting_external_tool",
  "paused",
]);

export const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set([
  "idle",
  "completed",
  "succeeded",
  "success",
  "done",
  "failed",
  "error",
  "cancelled",
  "canceled",
  "recoverable_failed",
  "degraded",
  "interrupted",
  "timed_out",
  "stopped",
  "terminated",
]);

export const TERMINAL_COMMAND_SESSION_STATUSES: ReadonlySet<string> = new Set([
  "idle",
  "completed",
  "succeeded",
  "success",
  "done",
  "failed",
  "error",
  "timed_out",
  "terminated",
  "stopped",
  "cancelled",
  "canceled",
  "interrupted",
]);

export const ACTIVE_COMMAND_SESSION_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "pending",
  "starting",
  "running",
  "awaiting_input",
  "render_stalled",
  "recoverable_stalled",
]);

const INTERRUPTIBLE_RUN_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "running",
  "waiting_input",
  "waiting_approval",
  "waiting_external_tool",
  "paused",
]);

export function normalizeRunStatus(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

export function isRecognizedRunStatus(value: unknown): boolean {
  const status = normalizeRunStatus(value);
  return ACTIVE_RUN_STATUSES.has(status) || TERMINAL_RUN_STATUSES.has(status);
}

export function isActiveRunStatus(value: unknown): boolean {
  return ACTIVE_RUN_STATUSES.has(normalizeRunStatus(value));
}

export function isTerminalRunStatus(value: unknown): boolean {
  return TERMINAL_RUN_STATUSES.has(normalizeRunStatus(value));
}

export function runStatusAllowsInterrupt(value: unknown): boolean {
  return INTERRUPTIBLE_RUN_STATUSES.has(normalizeRunStatus(value));
}

export function shouldApplyRunScopedStatus(
  eventRunIdValue: unknown,
  currentRunIdValue: unknown,
  pendingRunAcceptance = false,
): boolean {
  if (pendingRunAcceptance) return false;
  const eventRunId = String(eventRunIdValue || "").trim();
  const currentRunId = String(currentRunIdValue || "").trim();
  if (!currentRunId) return true;
  return Boolean(eventRunId) && eventRunId === currentRunId;
}

export function isActiveCommandSessionStatus(value: unknown): boolean {
  return ACTIVE_COMMAND_SESSION_STATUSES.has(normalizeRunStatus(value));
}

export type AuthoritativeRunActivityInput = {
  localStreamActive?: boolean;
  localRunId?: unknown;
  runtimeStatus?: unknown;
  runtimeRunId?: unknown;
  currentRunStatus?: unknown;
  currentRunId?: unknown;
  workflowStatus?: unknown;
  workflowRunId?: unknown;
  conversationStatus?: unknown;
  conversationRunId?: unknown;
};

/**
 * Engine projections outrank transport-local activity. A terminal snapshot or
 * realtime event must therefore settle a stale HTTP/submit stream immediately.
 */
export function deriveAuthoritativeRunActivity(input: AuthoritativeRunActivityInput): boolean {
  const localStreamActive = Boolean(input.localStreamActive);
  const localRunId = String(input.localRunId || "").trim();
  if (localStreamActive && !localRunId) {
    return true;
  }

  const candidates: Array<[unknown, unknown]> = [
    [input.runtimeStatus, input.runtimeRunId],
    [input.currentRunStatus, input.currentRunId],
    [input.workflowStatus, input.workflowRunId],
    [input.conversationStatus, input.conversationRunId],
  ];
  for (const [candidateStatus, candidateRunId] of candidates) {
    const status = normalizeRunStatus(candidateStatus);
    if (!status) continue;
    const runId = String(candidateRunId || "").trim();
    if (localStreamActive && localRunId && runId !== localRunId) continue;
    if (ACTIVE_RUN_STATUSES.has(status)) return true;
    if (TERMINAL_RUN_STATUSES.has(status)) return false;
  }
  return localStreamActive;
}

export type AuthoritativeRunControlInput = {
  authoritativeStatus?: unknown;
  optimisticStatus?: unknown;
  activeRunId?: unknown;
  historicalRunId?: unknown;
  hasPendingApproval?: boolean;
  hasActiveProcess?: boolean;
  controlCanInterrupt?: boolean;
  controlCanRetry?: boolean;
  controlCanResume?: boolean;
};

export function deriveAuthoritativeRunControl(input: AuthoritativeRunControlInput) {
  const authoritativeStatus = normalizeRunStatus(input.authoritativeStatus);
  const optimisticStatus = normalizeRunStatus(input.optimisticStatus) || "idle";
  const activeRunId = String(input.activeRunId || "").trim() || undefined;
  const historicalRunId = String(input.historicalRunId || activeRunId || "").trim() || undefined;
  const terminalStatus = TERMINAL_RUN_STATUSES.has(authoritativeStatus)
    ? authoritativeStatus
    : !ACTIVE_RUN_STATUSES.has(authoritativeStatus) && TERMINAL_RUN_STATUSES.has(optimisticStatus)
      ? optimisticStatus
      : "";
  const hasPendingApproval = Boolean(input.hasPendingApproval);
  const hasActiveProcess = Boolean(input.hasActiveProcess);
  const canRetry = Boolean(input.controlCanRetry);
  const canResume = Boolean(input.controlCanResume);
  const canInterrupt = Boolean(
    !terminalStatus
    && ((input.controlCanInterrupt && activeRunId) || hasActiveProcess),
  );

  let status = optimisticStatus;
  if (terminalStatus) {
    status = terminalStatus;
  } else if (hasPendingApproval) {
    status = "waiting_approval";
  } else if (authoritativeStatus === "waiting_input") {
    status = "waiting_input";
  } else if (!hasActiveProcess && canRetry && !ACTIVE_RUN_STATUSES.has(authoritativeStatus)) {
    status = "failed";
  } else if (!hasActiveProcess && canResume && !ACTIVE_RUN_STATUSES.has(authoritativeStatus)) {
    status = "paused";
  } else if (optimisticStatus === "running") {
    status = (
      (authoritativeStatus === "running" && Boolean(activeRunId))
      || Boolean(activeRunId)
      || canInterrupt
      || hasActiveProcess
    ) ? "running" : (authoritativeStatus || "idle");
  } else if (authoritativeStatus && optimisticStatus === "idle") {
    status = authoritativeStatus;
  }

  const keepRunId = Boolean(
    historicalRunId
    && (
      ACTIVE_RUN_STATUSES.has(status)
      || ["failed", "cancelled", "interrupted"].includes(status)
      || canRetry
      || canResume
    )
  );
  return {
    runId: keepRunId ? historicalRunId : undefined,
    status,
    canInterrupt,
    canRetry,
    canResume,
  };
}

export function terminalRunStatusFromTopic(topicValue: unknown, payloadValue?: unknown): string | null {
  const topic = normalizeRunStatus(topicValue);
  const payload = payloadValue && typeof payloadValue === "object"
    ? payloadValue as Record<string, unknown>
    : {};
  const declaredStatus = normalizeRunStatus(
    payload.status
    || payload.runStatus
    || payload.run_status
    || payload.toStatus
    || payload.to_status,
  );
  if (
    ["run.state.changed", "run.status.changed", "run.controlled"].includes(topic)
    && TERMINAL_RUN_STATUSES.has(declaredStatus)
    && declaredStatus !== "idle"
  ) {
    return declaredStatus;
  }
  const topicStatus: Record<string, string> = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.interrupted": "interrupted",
  };
  return topicStatus[topic] || null;
}
