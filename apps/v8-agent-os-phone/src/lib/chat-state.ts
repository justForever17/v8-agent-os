import type { ChatArtifact, ChatMessage, PendingApproval, PhoneUiTimelineNode } from "@/src/types/admin";
import { mergeTimelineNodesByIdentity } from "@v8/session-realtime";

const LOOPBACK_AVATAR_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\//i;
const ADMIN_AVATAR_PATH_PATTERN = /^\/Avatar\/[^?#]+$/i;
const DEFAULT_ADMIN_AVATAR_PATTERN = /(?:\/Avatar\/default-supervisor\.svg|\/brand-mark\.png)(?:$|[?#])/i;
export const DEFAULT_AGENT_AVATAR = "/brand-mark.png";

type ProjectedMessagePart = {
    type?: unknown;
    content?: unknown;
    time?: unknown;
    toolCallId?: unknown;
    toolName?: unknown;
    args?: unknown;
    result?: unknown;
    agentName?: unknown;
    agentAvatar?: unknown;
    agentRoleLabel?: unknown;
};

function hashMessageContent(value: string) {
    let hash = 0;
    for (let index = 0; index < value.length; index += 1) {
        hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
    }
    return Math.abs(hash).toString(36);
}

export function resolveAgentAvatar(value: unknown): string | undefined {
    const avatar = typeof value === "string" ? value.trim() : "";
    if (!avatar) {
        return undefined;
    }
    if (DEFAULT_ADMIN_AVATAR_PATTERN.test(avatar)) {
        return DEFAULT_AGENT_AVATAR;
    }
    if (ADMIN_AVATAR_PATH_PATTERN.test(avatar)) {
        return avatar;
    }
    if (LOOPBACK_AVATAR_PATTERN.test(avatar) && /\/Avatar\//i.test(avatar)) {
        try {
            const url = new URL(avatar);
            return url.pathname || avatar;
        } catch {
            return avatar;
        }
    }
    return avatar;
}

function buildArtifactKey(artifact: ChatArtifact) {
    return String(
        artifact.id
        || artifact.artifactId
        || artifact.workspacePath
        || artifact.sourcePath
        || artifact.previewUrl
        || artifact.externalUrl
        || `${artifact.kind || "artifact"}:${artifact.title || ""}`,
    ).trim();
}

function buildMessageIdentityKeys(message: ChatMessage) {
    const keys: string[] = [];
    const stableId = String(message.id || "").trim();
    if (stableId) {
        keys.push(`id:${stableId}`);
    }

    const normalizedContent = String(message.content || "").trim().replace(/\s+/g, " ");
    const semanticRunId = String(message.runId || message.metadata?.runId || "").trim();
    if (semanticRunId && message.role === "assistant") {
        keys.push(`run:${message.role}:${semanticRunId}`);
    }
    if (semanticRunId && message.role !== "user") {
        keys.push(`semantic:${message.role}:${semanticRunId}:${hashMessageContent(normalizedContent)}`);
    }

    return keys;
}

function mergeUniqueStrings(base: string[] = [], incoming: string[] = []) {
    return Array.from(new Set([...base, ...incoming].filter(Boolean)));
}

function mergeArtifacts(base: ChatArtifact[] = [], incoming: ChatArtifact[] = []) {
    const merged: ChatArtifact[] = [];
    const indexByKey = new Map<string, number>();

    for (const artifact of [...base, ...incoming]) {
        const key = buildArtifactKey(artifact);
        if (!key) {
            merged.push({ ...artifact });
            continue;
        }
        const existingIndex = indexByKey.get(key);
        if (existingIndex === undefined) {
            indexByKey.set(key, merged.length);
            merged.push({ ...artifact });
            continue;
        }
        merged[existingIndex] = {
            ...merged[existingIndex],
            ...artifact,
        };
    }

    return merged;
}

function mergeTimelineNodes(base: PhoneUiTimelineNode[] = [], incoming: PhoneUiTimelineNode[] = []) {
    return mergeTimelineNodesByIdentity(base, incoming) as PhoneUiTimelineNode[];
}

function normalizeMessageNodes(nodes: PhoneUiTimelineNode[] = []) {
    return mergeTimelineNodes([], nodes);
}

function mergeMessageRecords(existing: ChatMessage, incoming: ChatMessage): ChatMessage {
    const nextId = String(incoming.id || "").trim();
    const currentId = String(existing.id || "").trim();
    const preferIncomingId = nextId && (!currentId || currentId.startsWith("user-") || currentId.startsWith("assistant-"));
    const existingContent = String(existing.content || "");
    const incomingContent = String(incoming.content || "");
    const content = incomingContent.length >= existingContent.length ? incomingContent : existingContent;

    return {
        ...existing,
        ...incoming,
        id: preferIncomingId ? nextId : currentId || nextId || `message-${Date.now()}`,
        runId: incoming.runId || existing.runId,
        content,
        nodes: mergeTimelineNodes(existing.nodes || [], incoming.nodes || []),
        timestamp: Math.min(existing.timestamp || Date.now(), incoming.timestamp || Date.now()),
        images: mergeUniqueStrings(existing.images, incoming.images),
        artifacts: mergeArtifacts(existing.artifacts, incoming.artifacts),
        metadata: {
            ...(existing.metadata || {}),
            ...(incoming.metadata || {}),
        },
        agentName: incoming.agentName || existing.agentName,
        agentAvatar: resolveAgentAvatar(incoming.agentAvatar) || resolveAgentAvatar(existing.agentAvatar) || DEFAULT_AGENT_AVATAR,
        agentRoleLabel: incoming.agentRoleLabel || existing.agentRoleLabel,
        agentType: incoming.agentType || existing.agentType,
        uiEphemeral: typeof incoming.uiEphemeral === "boolean" ? incoming.uiEphemeral : existing.uiEphemeral,
        uiStreamPhase: incoming.uiStreamPhase || existing.uiStreamPhase,
    };
}

function normalizeProjectedPartsToNodes(message: ChatMessage) {
    const rawMessage = message as ChatMessage & { parts?: unknown };
    const parts = Array.isArray(rawMessage.parts) ? (rawMessage.parts as ProjectedMessagePart[]) : [];
    if (!parts.length) {
        return Array.isArray(message.nodes) ? normalizeMessageNodes(message.nodes.map((node) => ({ ...node }))) : [];
    }

    const timestamp = Number(message.timestamp || Date.now());
    const messageAgentName = message.agentName;
    const messageAgentAvatar = resolveAgentAvatar(message.agentAvatar) || (message.role === "assistant" ? DEFAULT_AGENT_AVATAR : undefined);
    const messageAgentRoleLabel = message.agentRoleLabel;

    const projectedNodes = parts.flatMap<PhoneUiTimelineNode>((part, index) => {
        const partType = String(part.type || "").trim();
        const nodeId = `${String(message.id || "projected").trim() || "projected"}-${index}`;
        const nodeAgentName = typeof part.agentName === "string" && part.agentName.trim() ? part.agentName.trim() : messageAgentName;
        const nodeAgentAvatar = resolveAgentAvatar(part.agentAvatar) || messageAgentAvatar;
        const nodeAgentRoleLabel = typeof part.agentRoleLabel === "string" && part.agentRoleLabel.trim()
            ? part.agentRoleLabel.trim()
            : messageAgentRoleLabel;
        const shared = {
            timestamp,
            agentName: nodeAgentName,
            agentAvatar: nodeAgentAvatar,
            agentRoleLabel: nodeAgentRoleLabel,
        };

        if (partType === "reasoning") {
            return [{
                id: nodeId,
                kind: "execution",
                executionType: "reasoning",
                content: typeof part.content === "string" ? part.content : "",
                time: typeof part.time === "number" ? part.time : 0,
                ...shared,
            } as PhoneUiTimelineNode];
        }

        if (partType === "tool_call") {
            return [{
                id: nodeId,
                kind: "execution",
                executionType: "tool_call",
                toolCallId: typeof part.toolCallId === "string" ? part.toolCallId : undefined,
                toolName: typeof part.toolName === "string" ? part.toolName : undefined,
                args: part.args,
                ...shared,
            } as PhoneUiTimelineNode];
        }

        if (partType === "tool_result") {
            return [{
                id: nodeId,
                kind: "execution",
                executionType: "tool_result",
                toolCallId: typeof part.toolCallId === "string" ? part.toolCallId : undefined,
                toolName: typeof part.toolName === "string" ? part.toolName : undefined,
                result: part.result,
                ...shared,
            } as PhoneUiTimelineNode];
        }

        if (partType === "agent_start") {
            return [{
                id: nodeId,
                kind: "execution",
                executionType: "agent_start",
                ...shared,
            } as PhoneUiTimelineNode];
        }

        if (partType === "text") {
            return [{
                id: nodeId,
                kind: "narrative",
                role: message.role === "assistant" || message.role === "system" || message.role === "user" ? message.role : "assistant",
                content: typeof part.content === "string" ? part.content : "",
                ...shared,
            } as PhoneUiTimelineNode];
        }

        return [];
    });

    return normalizeMessageNodes(projectedNodes);
}

function buildRenderKey(message: ChatMessage) {
    const id = String(message.id || "").trim();
    const runId = String(message.runId || message.metadata?.runId || "").trim();
    const content = String(message.content || "").trim().replace(/\s+/g, " ");
    if (id) {
        return id;
    }
    if (runId) {
        return `${message.role}:${runId}:${hashMessageContent(content)}`;
    }
    const timestamp = Number(message.timestamp || 0);
    return `${message.role}:${timestamp}:${hashMessageContent(content)}`;
}

export function normalizeMessagesForState(messages: ChatMessage[]) {
    const merged: ChatMessage[] = [];
    const indexByKey = new Map<string, number>();

    for (const message of messages) {
        const candidate: ChatMessage = {
            ...message,
            agentName: message.role === "assistant" ? (message.agentName || "智能主管") : message.agentName,
            agentAvatar: resolveAgentAvatar(message.agentAvatar) || (message.role === "assistant" ? DEFAULT_AGENT_AVATAR : undefined),
            agentRoleLabel: message.role === "assistant" ? (message.agentRoleLabel || "主理人") : message.agentRoleLabel,
            agentType: message.role === "assistant" ? (message.agentType || "supervisor") : message.agentType,
            nodes: normalizeProjectedPartsToNodes(message),
            images: Array.isArray(message.images) ? [...message.images] : [],
            artifacts: Array.isArray(message.artifacts) ? message.artifacts.map((artifact) => ({ ...artifact })) : [],
            metadata: message.metadata ? { ...message.metadata } : undefined,
            uiEphemeral: message.uiEphemeral,
            uiStreamPhase: message.uiStreamPhase,
        };
        const keys = buildMessageIdentityKeys(candidate);
        const existingIndex = keys
            .map((key) => indexByKey.get(key))
            .find((index): index is number => index !== undefined);

        if (existingIndex === undefined) {
            const nextIndex = merged.length;
            merged.push(candidate);
            keys.forEach((key) => indexByKey.set(key, nextIndex));
            continue;
        }

        merged[existingIndex] = mergeMessageRecords(merged[existingIndex], candidate);
        buildMessageIdentityKeys(merged[existingIndex]).forEach((key) => indexByKey.set(key, existingIndex));
    }

    const seenRenderKeys = new Map<string, number>();
    return merged.map((message) => {
        const baseRenderKey = buildRenderKey(message);
        const duplicateIndex = seenRenderKeys.get(baseRenderKey) || 0;
        seenRenderKeys.set(baseRenderKey, duplicateIndex + 1);
        return {
            ...message,
            renderKey: duplicateIndex > 0 ? `${baseRenderKey}:${duplicateIndex}` : baseRenderKey,
        };
    });
}

export function upsertApproval(current: PendingApproval[], incoming: PendingApproval) {
    const incomingId = String(incoming.id || incoming.approval_id || "").trim();
    if (!incomingId) {
        return [incoming, ...current];
    }

    const next = [...current];
    const existingIndex = next.findIndex((item) => String(item.id || item.approval_id || "").trim() === incomingId);
    if (existingIndex >= 0) {
        next[existingIndex] = {
            ...next[existingIndex],
            ...incoming,
            request: {
                ...(next[existingIndex].request || {}),
                ...(incoming.request || {}),
            },
        };
        return next;
    }
    return [incoming, ...next];
}
