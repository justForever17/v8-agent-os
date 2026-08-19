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

export type TerminalProjectionInput = {
    localRunId?: unknown;
    acceptancePending?: boolean;
    runtimeStatus?: unknown;
    projectedRunId?: unknown;
    currentRunStatus?: unknown;
    currentRunId?: unknown;
    controlStatus?: unknown;
    controlRunId?: unknown;
    workflowStatus?: unknown;
    workflowRunId?: unknown;
};

export type MatchingTerminalProjection = {
    runId: string;
    status: string;
};

export type SubmittedRunSettlementInput = {
    submittedRunId?: unknown;
    terminalRunId?: unknown;
    acceptancePending?: boolean;
};

export type EmptyHistoryReconciliationInput = {
    preserveCurrentOnEmpty?: boolean;
    currentMessageCount?: unknown;
    incomingMessageCount?: unknown;
};

export function deriveInterruptibleRunId(input: InterruptibleRunInput): string | null {
    const runId = String(input.controlRunId || input.currentRunId || "").trim();
    if (!runId) return null;
    if (input.controlCanInterrupt === true) return runId;
    const status = normalizeRunStatus(input.currentRunStatus || input.runtimeStatus);
    return runStatusAllowsInterrupt(status) ? runId : null;
}

/**
 * Resolve a terminal snapshot only when its authoritative run identity matches
 * the locally submitted run. The first recognized projection status wins so a
 * stale nested terminal record cannot override a newer active runtime status.
 */
export function deriveMatchingTerminalProjection(
    input: TerminalProjectionInput,
): MatchingTerminalProjection | null {
    if (input.acceptancePending) return null;

    const projectedRunId = String(
        input.projectedRunId
        || input.controlRunId
        || input.currentRunId
        || input.workflowRunId
        || "",
    ).trim();
    const candidates: Array<[unknown, unknown]> = [
        [input.runtimeStatus, projectedRunId],
        [input.currentRunStatus, input.currentRunId],
        [input.controlStatus, input.controlRunId],
        [input.workflowStatus, input.workflowRunId],
    ];

    for (const [candidateStatus, candidateRunId] of candidates) {
        const status = normalizeRunStatus(candidateStatus);
        if (!isRecognizedRunStatus(status)) continue;
        if (!isTerminalRunStatus(status)) return null;

        const runId = String(candidateRunId || "").trim();
        if (!runId || !shouldApplyRunScopedStatus(runId, input.localRunId, false)) {
            return null;
        }
        return { runId, status };
    }

    return null;
}

export function shouldSettleSubmittedRun(input: SubmittedRunSettlementInput): boolean {
    if (input.acceptancePending) return false;
    const submittedRunId = String(input.submittedRunId || "").trim();
    if (!submittedRunId) return false;
    const terminalRunId = String(input.terminalRunId || "").trim();
    return Boolean(terminalRunId) && terminalRunId === submittedRunId;
}

export function shouldPreserveCurrentHistoryOnEmpty(
    input: EmptyHistoryReconciliationInput,
): boolean {
    return Boolean(
        input.preserveCurrentOnEmpty
        && Number(input.currentMessageCount || 0) > 0
        && Number(input.incomingMessageCount || 0) === 0,
    );
}

/**
 * The runtime projection is the current-session truth. Sidebar conversation state
 * is only a cold-start fallback because it can lag behind terminal run events.
 */
export function deriveComposerRunActivity(input: ComposerRunActivityInput) {
    return deriveAuthoritativeRunActivity(input);
}
