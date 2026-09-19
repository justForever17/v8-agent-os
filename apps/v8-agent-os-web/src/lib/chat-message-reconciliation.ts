import type { Message } from "@/store/chat-types";
import {
    normalizeMessagesForState,
    normalizeProjectedMessages,
} from "@/lib/chat-stream-state";

function hasStructuredAssistantPayload(message: Message | null | undefined): boolean {
    return Boolean(
        message
        && message.role === "assistant"
        && (
            (Array.isArray(message.nodes) && message.nodes.length > 0)
            || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
            || (Array.isArray(message.images) && message.images.length > 0)
        ),
    );
}

function hasRenderableWebMessagePayload(message: Message | null | undefined): boolean {
    const metadata = message?.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : {};
    return Boolean(
        message
        && (
            String(message.content || "").trim()
            || (Array.isArray(message.nodes) && message.nodes.length > 0)
            || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
            || (Array.isArray(message.images) && message.images.length > 0)
            || (metadata.composerPresentation && typeof metadata.composerPresentation === "object")
            || (Array.isArray(metadata.contextMentions) && metadata.contextMentions.length > 0)
            || (Array.isArray(metadata.pluginReferences) && metadata.pluginReferences.length > 0)
        ),
    );
}

export function attachSseEventId(payload: unknown, event: MessageEvent): unknown {
    const eventId = String(event.lastEventId || "").trim();
    if (!eventId || !payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
    const record = payload as Record<string, unknown>;
    return {
        ...record,
        _diagnostics: {
            ...((record._diagnostics && typeof record._diagnostics === "object")
                ? record._diagnostics as Record<string, unknown>
                : {}),
            sseEventId: eventId,
        },
    };
}

function buildWebMessageComparisonKeys(message: Message): string[] {
    const keys = new Set<string>();
    const id = String(message.id || "").trim();
    const runId = String(message.runId || "").trim();
    const role = String(message.role || "").trim();
    const timestamp = Number(message.timestamp || 0) || 0;
    if (id) keys.add(`id:${id}`);
    if (runId && role) keys.add(`run:${runId}:${role}`);
    if (role && timestamp > 0) keys.add(`role:${role}:ts:${timestamp}`);
    return Array.from(keys);
}

function mergeWebMessagePayload(base: Message, incoming: Message): Message {
    const incomingTranscriptVersion = Number((incoming.metadata || {}).transcriptVersion || 0);
    const incomingCanonical = incomingTranscriptVersion > 0 || (incoming.nodes?.length || 0) > 0;
    const merged: Message = {
        ...base,
        ...incoming,
        metadata: { ...(base.metadata || {}), ...(incoming.metadata || {}) },
    };
    if (!incomingCanonical && (base.nodes?.length || 0) > (incoming.nodes?.length || 0)) merged.nodes = base.nodes;
    if (!incomingCanonical && (base.artifacts?.length || 0) > (incoming.artifacts?.length || 0)) merged.artifacts = base.artifacts;
    if (!incomingCanonical && (base.images?.length || 0) > (incoming.images?.length || 0)) merged.images = base.images;
    if (!merged.agentName && base.agentName) merged.agentName = base.agentName;
    if (!merged.agentAvatar && base.agentAvatar) merged.agentAvatar = base.agentAvatar;
    if (!merged.agentRoleLabel && base.agentRoleLabel) merged.agentRoleLabel = base.agentRoleLabel;
    if (!merged.toolInvocations?.length && base.toolInvocations?.length) merged.toolInvocations = base.toolInvocations;
    return merged;
}

/** Merge a durable projection without erasing a newer local stream or draft. */
export function mergeProjectedSnapshotMessages(current: Message[], projectedMessages: unknown[]): Message[] {
    const normalizedSnapshot = normalizeProjectedMessages(projectedMessages);
    const receivedRuns = new Map(normalizedSnapshot
        .filter((message) => message.role === "user" && message.runId)
        .flatMap((message) => [message.id, String(message.metadata?.clientMessageId || "")]
            .filter(Boolean).map((id) => [id, message.runId!] as const)));
    current = current.map((message) => {
        const runId = receivedRuns.get(String(message.metadata?.clientMessageId || ""));
        return message.role === "assistant" && message.uiEphemeral && runId && (!message.runId || message.runId === runId)
            ? { ...message, runId }
            : message;
    });
    if (current.length === 0) return normalizeMessagesForState(normalizedSnapshot);

    const currentByKey = new Map<string, Message>();
    current.forEach((message) => buildWebMessageComparisonKeys(message).forEach((key) => {
        if (!currentByKey.has(key)) currentByKey.set(key, message);
    }));
    const matchedCurrent = new Set<Message>();
    const mergedSnapshot = normalizedSnapshot.map((snapshotMessage) => {
        const matchingCurrent = buildWebMessageComparisonKeys(snapshotMessage)
            .map((key) => currentByKey.get(key)).find(Boolean);
        if (!matchingCurrent) return snapshotMessage;
        matchedCurrent.add(matchingCurrent);
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) return normalizeMessagesForState([matchingCurrent, snapshotMessage])[0] || snapshotMessage;
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant"
            && hasRenderableWebMessagePayload(snapshotMessage)
            && hasStructuredAssistantPayload(snapshotMessage);
        if (!snapshotAuthoritativeAssistant) return mergeWebMessagePayload(matchingCurrent, snapshotMessage);
        return {
            ...snapshotMessage,
            metadata: { ...(matchingCurrent.metadata || {}), ...(snapshotMessage.metadata || {}) },
            images: (snapshotMessage.images?.length || 0) > 0 ? snapshotMessage.images : matchingCurrent.images,
            artifacts: (snapshotMessage.artifacts?.length || 0) > 0 ? snapshotMessage.artifacts : matchingCurrent.artifacts,
            toolInvocations: (snapshotMessage.toolInvocations?.length || 0) > 0 ? snapshotMessage.toolInvocations : matchingCurrent.toolInvocations,
        };
    });
    const retainedHistory = current.filter((message) => !matchedCurrent.has(message));
    return normalizeMessagesForState([...retainedHistory, ...mergedSnapshot]);
}
