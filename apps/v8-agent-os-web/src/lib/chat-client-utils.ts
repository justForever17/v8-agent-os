export const CONTEXT_SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/;

export type ContextSessionReference = {
    sessionId: string;
    source: "history_menu";
};

export type ChatQueueSubmitResponse = {
    accepted?: boolean;
    session_id?: string;
    conversationId?: string;
    queued?: boolean;
    queuedMessage?: import("@/lib/chat-queue").QueuedChatMessage | null;
    clientMessageId?: string;
    run_id?: string;
    runId?: string;
    error?: string;
};

export function asPlainRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

export function readString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

export function earlierTimestamp(left?: string, right?: string): string | undefined {
    if (!left) return right;
    if (!right) return left;
    const leftTime = Date.parse(left);
    const rightTime = Date.parse(right);
    if (!Number.isFinite(leftTime)) return right;
    if (!Number.isFinite(rightTime)) return left;
    return leftTime <= rightTime ? left : right;
}

export function isLegacyChatUnsupportedPayload(value: unknown): boolean {
    const root = asPlainRecord(value);
    const snapshot = asPlainRecord(root.snapshot);
    return Boolean(root.legacyChatUnsupported || snapshot.legacyChatUnsupported);
}

export function isWorkspaceBindingErrorMessage(value: unknown): boolean {
    const text = String(value || "").toLowerCase();
    return text.includes("workspace_binding_required")
        || text.includes("workspace_trust_required")
        || text.includes("workspace_side_effect_blocked");
}

export function readErrorPayloadMessage(payload: Record<string, unknown>): string {
    const detail = asPlainRecord(payload.detail);
    return readString(detail.error)
        || readString(detail.summary)
        || readString(detail.recommendedNextAction)
        || readString(payload.error)
        || readString(payload.message);
}

