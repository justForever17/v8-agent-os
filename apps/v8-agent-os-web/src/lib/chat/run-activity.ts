import {
    deriveAuthoritativeRunActivity,
    isActiveRunStatus,
    isRecognizedRunStatus,
    normalizeRunStatus,
    runStatusAllowsInterrupt,
    shouldApplyRunScopedStatus,
    isTerminalRunStatus,
    terminalRunStatusFromTopic,
} from "@v8/session-realtime/run-status";

export {
    isActiveRunStatus,
    isRecognizedRunStatus,
    isTerminalRunStatus,
    runStatusAllowsInterrupt,
    shouldApplyRunScopedStatus,
    terminalRunStatusFromTopic,
};

export type ComposerRunActivityInput = {
    localStreamActive: boolean;
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

export type InterruptibleRunInput = {
    controlRunId?: unknown;
    currentRunId?: unknown;
    controlCanInterrupt?: unknown;
    currentRunStatus?: unknown;
    runtimeStatus?: unknown;
};

export function deriveInterruptibleRunId(input: InterruptibleRunInput): string | null {
    const runId = String(input.controlRunId || input.currentRunId || "").trim();
    if (!runId) return null;
    if (input.controlCanInterrupt === true) return runId;
    const status = normalizeRunStatus(input.currentRunStatus || input.runtimeStatus);
    return runStatusAllowsInterrupt(status) ? runId : null;
}

/**
 * The runtime projection is the current-session truth. Sidebar conversation state
 * is only a cold-start fallback because it can lag behind terminal run events.
 */
export function deriveComposerRunActivity(input: ComposerRunActivityInput) {
    return deriveAuthoritativeRunActivity(input);
}
