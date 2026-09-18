export type QueuedChatMessage = {
    id: string;
    sessionId?: string;
    runId?: string;
    clientMessageId?: string;
    content: string;
    state?: "pending" | "promoted" | "injected" | "consumed" | "cancelled" | string;
    ordinal?: number;
    createdAt?: string;
    updatedAt?: string;
    promotedAt?: string;
    injectedAt?: string;
    consumedAt?: string;
    cancelledAt?: string;
};

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function readString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** Normalize the two API spellings once at the queue boundary. */
export function normalizeQueuedMessage(value: unknown): QueuedChatMessage | null {
    const record = asRecord(value);
    const id = readString(record.id)
        || readString(record.queueMessageId)
        || readString(record.guidanceQueueMessageId);
    if (!id) return null;

    const ordinalValue = Number(record.ordinal);
    return {
        id,
        sessionId: readString(record.sessionId) || readString(record.session_id) || undefined,
        runId: readString(record.runId) || readString(record.run_id) || undefined,
        clientMessageId: readString(record.clientMessageId) || readString(record.client_message_id) || undefined,
        content: readString(record.content) || readString(record.text) || readString(record.message),
        state: readString(record.state) || readString(record.status) || "pending",
        ordinal: Number.isFinite(ordinalValue) ? ordinalValue : undefined,
        createdAt: readString(record.createdAt) || readString(record.created_at) || undefined,
        updatedAt: readString(record.updatedAt) || readString(record.updated_at) || undefined,
        promotedAt: readString(record.promotedAt) || readString(record.promoted_at) || undefined,
        injectedAt: readString(record.injectedAt) || readString(record.injected_at) || undefined,
        consumedAt: readString(record.consumedAt) || readString(record.consumed_at) || undefined,
        cancelledAt: readString(record.cancelledAt) || readString(record.cancelled_at) || undefined,
    };
}

export function extractQueuedMessages(value: unknown): QueuedChatMessage[] | null {
    const root = asRecord(value);
    const snapshot = asRecord(root.snapshot);
    for (const candidate of [root.queuedMessages, snapshot.queuedMessages]) {
        if (!Array.isArray(candidate)) continue;
        return candidate.map(normalizeQueuedMessage).filter((item): item is QueuedChatMessage => Boolean(item));
    }
    return null;
}

export function isVisibleQueuedMessage(item: QueuedChatMessage): boolean {
    return !["cancelled", "consumed", "injected"].includes(String(item.state || "pending").trim().toLowerCase());
}

export function sortQueuedMessages(items: QueuedChatMessage[]): QueuedChatMessage[] {
    return [...items].sort((left, right) => {
        const leftOrdinal = Number(left.ordinal);
        const rightOrdinal = Number(right.ordinal);
        if (Number.isFinite(leftOrdinal) && Number.isFinite(rightOrdinal) && leftOrdinal !== rightOrdinal) {
            return leftOrdinal - rightOrdinal;
        }
        return String(left.createdAt || left.updatedAt || left.id)
            .localeCompare(String(right.createdAt || right.updatedAt || right.id));
    });
}

