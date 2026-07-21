const ACTIVE_RUN_STATUSES = new Set([
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

const TERMINAL_RUN_STATUSES = new Set([
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
    "stopped",
    "terminated",
]);

export type ComposerRunActivityInput = {
    localStreamActive: boolean;
    runtimeStatus?: unknown;
    currentRunStatus?: unknown;
    workflowStatus?: unknown;
    conversationStatus?: unknown;
};

function normalizeStatus(value: unknown) {
    return String(value || "").trim().toLowerCase();
}

export function runStatusAllowsInterrupt(value: unknown) {
    return new Set(["running", "waiting_approval", "waiting_external_tool", "paused"]).has(normalizeStatus(value));
}

export function isRecognizedRunStatus(value: unknown) {
    const status = normalizeStatus(value);
    return ACTIVE_RUN_STATUSES.has(status) || TERMINAL_RUN_STATUSES.has(status);
}

/**
 * The runtime projection is the current-session truth. Sidebar conversation state
 * is only a cold-start fallback because it can lag behind terminal run events.
 */
export function deriveComposerRunActivity(input: ComposerRunActivityInput) {
    if (input.localStreamActive) return true;

    for (const candidate of [input.runtimeStatus, input.currentRunStatus, input.workflowStatus]) {
        const status = normalizeStatus(candidate);
        if (!status) continue;
        if (ACTIVE_RUN_STATUSES.has(status)) return true;
        if (TERMINAL_RUN_STATUSES.has(status)) return false;
    }

    const conversationStatus = normalizeStatus(input.conversationStatus);
    if (ACTIVE_RUN_STATUSES.has(conversationStatus)) return true;
    if (TERMINAL_RUN_STATUSES.has(conversationStatus)) return false;
    return false;
}

export function terminalRunStatusFromTopic(topicValue: unknown, payloadValue?: unknown): string | null {
    const topic = normalizeStatus(topicValue);
    const payload = payloadValue && typeof payloadValue === "object"
        ? payloadValue as Record<string, unknown>
        : {};
    const declaredStatus = normalizeStatus(
        payload.status
        || payload.runStatus
        || payload.run_status
        || payload.toStatus
        || payload.to_status,
    );
    if (
        ["run.state.changed", "run.status.changed"].includes(topic)
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
