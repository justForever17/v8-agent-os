import { Message, UiTimelineNode, UiNarrativeNode, UiExecutionNode, UiGovernanceNode, UiArtifactNode } from '@/store/chat-types';
import { RuntimeArtifact, normalizeRuntimeArtifact, normalizeRuntimeArtifacts } from '@/lib/artifacts';
import { createClientId } from '@/lib/id';

export type AgentProfile = {
    agentName?: string;
    agentAvatar?: string;
    agentRoleLabel?: string;
};

type ProjectedPartType = 'text' | 'reasoning' | 'tool_call' | 'tool_result' | 'agent_start';

type ProjectedMessagePart = {
    type?: ProjectedPartType;
    content?: unknown;
    time?: unknown;
    agentName?: unknown;
    agentAvatar?: unknown;
    agentRoleLabel?: unknown;
    toolCallId?: unknown;
    toolName?: unknown;
    args?: unknown;
    result?: unknown;
};

type ProjectedMessageRecord = {
    id?: unknown;
    role?: unknown;
    runId?: unknown;
    content?: unknown;
    parts?: unknown;
    timestamp?: unknown;
    agentName?: unknown;
    agentAvatar?: unknown;
    agentRoleLabel?: unknown;
    agentType?: unknown;
    images?: unknown;
    artifacts?: unknown;
    metadata?: unknown;
};

export type RealtimeUiEvent = {
    type: string;
    name?: string;
    data?: Record<string, unknown>;
    run_id?: string;
    content?: string;
    agent?: {
        id?: string;
        name?: string;
        avatar?: string;
        roleLabel?: string;
    };
    tool?: {
        toolCallId?: string;
        toolName?: string;
        args?: unknown;
        result?: unknown;
    };
    artifact?: RuntimeArtifact;
};

const LOOPBACK_AVATAR_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\//i;
const ADMIN_AVATAR_PATH_PATTERN = /^\/Avatar\/[^?#]+$/i;
const DEFAULT_ADMIN_AVATAR_PATTERN = /(?:\/Avatar\/default-supervisor\.svg|\/brand-mark\.png)(?:$|[?#])/i;
export const DEFAULT_AVATAR = "/brand-mark.png";

function buildAvatarProxyUrl(avatar: string): string {
    return `/api/avatar?src=${encodeURIComponent(avatar)}`;
}

function resolveAgentAvatar(value: unknown): string | undefined {
    const avatar = typeof value === "string" ? value.trim() : "";
    if (!avatar) {
        return undefined;
    }

    if (DEFAULT_ADMIN_AVATAR_PATTERN.test(avatar)) {
        return DEFAULT_AVATAR;
    }

    if (ADMIN_AVATAR_PATH_PATTERN.test(avatar)) {
        return buildAvatarProxyUrl(avatar);
    }

    if (LOOPBACK_AVATAR_PATTERN.test(avatar) && /\/Avatar\//i.test(avatar)) {
        return buildAvatarProxyUrl(avatar);
    }

    return avatar;
}

export function cloneMessages(messages: Message[]): Message[] {
    return messages.map((message) => ({
        ...message,
        nodes: Array.isArray(message.nodes)
            ? message.nodes.map((node) => ({ ...node }))
            : [],
        images: Array.isArray(message.images) ? [...message.images] : [],
        artifacts: Array.isArray(message.artifacts) ? message.artifacts.map((artifact) => ({ ...artifact })) : [],
        metadata: message.metadata ? { ...message.metadata } : undefined,
    }));
}

export function buildAssistantMessage(activeAgentProfile: AgentProfile): Message {
    return {
        id: createClientId('message'),
        role: 'assistant',
        content: '',
        nodes: [],
        artifacts: [],
        agentName: activeAgentProfile.agentName || '智能主管',
        agentAvatar: resolveAgentAvatar(activeAgentProfile.agentAvatar) || DEFAULT_AVATAR,
        agentRoleLabel: activeAgentProfile.agentRoleLabel || '主理人',
        timestamp: Date.now(),
    };
}

function hashMessageContent(value: string): string {
    let hash = 0;
    for (let index = 0; index < value.length; index += 1) {
        hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
    }
    return Math.abs(hash).toString(36);
}

function buildMessageIdentityKeys(message: Message): string[] {
    const keys: string[] = [];
    const stableId = String(message.id || '').trim();
    if (stableId) {
        keys.push(`id:${stableId}`);
    }

    const normalizedContent = String(message.content || '').trim().replace(/\s+/g, ' ');
    const semanticRunId = String(message.runId || message.metadata?.runId || '').trim();
    if (semanticRunId && message.role !== 'user') {
        keys.push(`semantic:${message.role}:${semanticRunId}:${hashMessageContent(normalizedContent)}`);
    }

    return keys;
}

function mergeUniqueStrings(base: string[] = [], incoming: string[] = []): string[] {
    return Array.from(new Set([...base, ...incoming].filter(Boolean)));
}

function mergeArtifacts(
    base: RuntimeArtifact[] = [],
    incoming: RuntimeArtifact[] = [],
): RuntimeArtifact[] {
    const merged: RuntimeArtifact[] = [];
    const indexByKey = new Map<string, number>();
    const buildKey = (artifact: RuntimeArtifact) =>
        artifact.id
            || artifact.previewUrl
            || artifact.externalUrl
            || artifact.sourcePath
            || `${artifact.kind}:${artifact.title || artifact.displayLabel || ''}`;

    for (const artifact of [...base, ...incoming]) {
        const normalized = normalizeRuntimeArtifact(artifact);
        if (!normalized) continue;
        const key = buildKey(normalized);
        const existingIndex = indexByKey.get(key);
        if (existingIndex === undefined) {
            indexByKey.set(key, merged.length);
            merged.push({ ...normalized });
            continue;
        }
        merged[existingIndex] = {
            ...merged[existingIndex],
            ...normalized,
        };
    }

    return merged;
}

function mergeTimelineNodes(base: UiTimelineNode[] = [], incoming: UiTimelineNode[] = []): UiTimelineNode[] {
    const merged: UiTimelineNode[] = [];
    const indexById = new Map<string, number>();
    for (const node of [...base, ...incoming]) {
        const nodeId = String(node.id || '').trim();
        if (!nodeId) {
            merged.push({ ...node });
            continue;
        }
        const existingIndex = indexById.get(nodeId);
        if (existingIndex === undefined) {
            indexById.set(nodeId, merged.length);
            merged.push({ ...node });
            continue;
        }
        merged[existingIndex] = {
            ...merged[existingIndex],
            ...node,
        } as UiTimelineNode;
    }
    return merged;
}

function mergeMessageRecords(existing: Message, incoming: Message): Message {
    const nextId = String(incoming.id || '').trim();
    const currentId = String(existing.id || '').trim();
    const preferIncomingId = nextId
        && (
            !currentId
            || currentId.startsWith('message_')
            || currentId.startsWith('client_')
        );
    const existingContent = String(existing.content || '');
    const incomingContent = String(incoming.content || '');
    const content = incomingContent.length >= existingContent.length ? incomingContent : existingContent;

    return {
        ...existing,
        ...incoming,
        id: preferIncomingId ? nextId : currentId || nextId || createClientId('message'),
        role: existing.role || incoming.role,
        runId: incoming.runId || existing.runId,
        content,
        nodes: mergeTimelineNodes(existing.nodes || [], incoming.nodes || []),
        timestamp: Math.min(existing.timestamp || Date.now(), incoming.timestamp || Date.now()),
        agentName: incoming.agentName || existing.agentName,
        agentAvatar: resolveAgentAvatar(incoming.agentAvatar) || resolveAgentAvatar(existing.agentAvatar) || DEFAULT_AVATAR,
        agentRoleLabel: incoming.agentRoleLabel || existing.agentRoleLabel,
        agentType: incoming.agentType || existing.agentType,
        images: mergeUniqueStrings(existing.images, incoming.images),
        artifacts: mergeArtifacts(existing.artifacts, incoming.artifacts),
        metadata: {
            ...(existing.metadata || {}),
            ...(incoming.metadata || {}),
        },
        toolInvocations: incoming.toolInvocations || existing.toolInvocations,
    };
}

export function normalizeMessagesForState(messages: Message[]): Message[] {
    const merged: Message[] = [];
    const indexByKey = new Map<string, number>();

    for (const message of messages) {
        const candidate: Message = {
            ...message,
            agentAvatar: resolveAgentAvatar(message.agentAvatar),
            nodes: Array.isArray(message.nodes) ? message.nodes.map((node) => ({ ...node })) : [],
            images: Array.isArray(message.images) ? [...message.images] : [],
            artifacts: Array.isArray(message.artifacts) ? message.artifacts.map((artifact) => ({ ...artifact })) : [],
            metadata: message.metadata ? { ...message.metadata } : undefined,
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

    return merged;
}

export function mergeMessageCollections(base: Message[], incoming: Message[]): Message[] {
    return normalizeMessagesForState([...base, ...incoming]);
}

export function normalizeProjectedMessages(input: unknown[]): Message[] {
    return normalizeMessagesForState(input.map((raw) => {
        const msg = (raw || {}) as ProjectedMessageRecord;
        const role = msg.role === 'assistant' || msg.role === 'user' || msg.role === 'system' || msg.role === 'tool'
            ? msg.role
            : 'assistant';
        const timestamp = typeof msg.timestamp === 'number' ? msg.timestamp : Date.now();
        const messageAgentProfile = {
            agentName: typeof msg.agentName === 'string' ? msg.agentName : undefined,
            agentAvatar: resolveAgentAvatar(msg.agentAvatar),
            agentRoleLabel: typeof msg.agentRoleLabel === 'string' ? msg.agentRoleLabel : undefined,
        };
        const parts = Array.isArray(msg.parts) ? (msg.parts as ProjectedMessagePart[]) : [];
        const nodes: UiTimelineNode[] = parts.flatMap<UiTimelineNode>((part, index) => {
            const nodeAgentProfile = {
                agentName: typeof part.agentName === 'string' ? part.agentName : messageAgentProfile.agentName,
                agentAvatar: resolveAgentAvatar(part.agentAvatar) || messageAgentProfile.agentAvatar,
                agentRoleLabel: typeof part.agentRoleLabel === 'string' ? part.agentRoleLabel : messageAgentProfile.agentRoleLabel,
            };
            const nodeId = `${typeof msg.id === 'string' ? msg.id : 'projected'}-${index}`;

            if (part.type === 'reasoning') {
                return [{
                    id: nodeId,
                    kind: 'execution',
                    executionType: 'reasoning',
                    content: typeof part.content === 'string' ? part.content : '',
                    time: typeof part.time === 'number' ? part.time : 0,
                    timestamp,
                    ...nodeAgentProfile,
                } as UiExecutionNode];
            }

            if (part.type === 'tool_call') {
                return [{
                    id: nodeId,
                    kind: 'execution',
                    executionType: 'tool_call',
                    toolCallId: typeof part.toolCallId === 'string' ? part.toolCallId : undefined,
                    toolName: typeof part.toolName === 'string' ? part.toolName : undefined,
                    args: part.args,
                    timestamp,
                    ...nodeAgentProfile,
                } as UiExecutionNode];
            }

            if (part.type === 'tool_result') {
                return [{
                    id: nodeId,
                    kind: 'execution',
                    executionType: 'tool_result',
                    toolCallId: typeof part.toolCallId === 'string' ? part.toolCallId : undefined,
                    result: part.result,
                    timestamp,
                    ...nodeAgentProfile,
                } as UiExecutionNode];
            }

            if (part.type === 'agent_start') {
                return [{
                    id: nodeId,
                    kind: 'execution',
                    executionType: 'agent_start',
                    timestamp,
                    ...nodeAgentProfile,
                } as UiExecutionNode];
            }

            if (part.type === 'text') {
                return [{
                    id: nodeId,
                    kind: 'narrative',
                    role: role === 'assistant' || role === 'system' || role === 'user' ? role : 'assistant',
                    content: typeof part.content === 'string' ? part.content : '',
                    timestamp,
                    ...nodeAgentProfile,
                } as UiNarrativeNode];
            }

            return [];
        });

        return {
            id: typeof msg.id === 'string' ? msg.id : createClientId('message'),
            role,
            runId: typeof msg.runId === 'string' ? msg.runId : undefined,
            content: typeof msg.content === 'string' ? msg.content : '',
            nodes,
            timestamp,
            agentName: messageAgentProfile.agentName,
            agentAvatar: messageAgentProfile.agentAvatar,
            agentRoleLabel: messageAgentProfile.agentRoleLabel,
            agentType: msg.agentType === 'supervisor' || msg.agentType === 'agent' || msg.agentType === 'user'
                ? msg.agentType
                : undefined,
            images: Array.isArray(msg.images) ? [...msg.images] as string[] : [],
            artifacts: normalizeRuntimeArtifacts(msg.artifacts),
            metadata: msg.metadata && typeof msg.metadata === 'object'
                ? { ...(msg.metadata as Record<string, unknown>) }
                : undefined,
        };
    }) as Message[]);
}

export function deriveRealtimeStreamState(messages: Message[]): {
    currentAiMsg: Message | undefined;
    activeAgentProfile: AgentProfile;
} {
    const lastAssistant = [...messages].reverse().find((msg) => msg.role === 'assistant');
    if (!lastAssistant) {
        return {
            currentAiMsg: undefined,
            activeAgentProfile: {},
        };
    }

    const lastAgentNode = [...(lastAssistant.nodes || [])]
        .reverse()
        .find((node) => node.agentName || node.agentAvatar || node.agentRoleLabel);

    return {
        currentAiMsg: lastAssistant,
        activeAgentProfile: {
            agentName: lastAgentNode?.agentName || lastAssistant.agentName,
            agentAvatar: resolveAgentAvatar(lastAgentNode?.agentAvatar) || resolveAgentAvatar(lastAssistant.agentAvatar),
            agentRoleLabel: lastAgentNode?.agentRoleLabel || lastAssistant.agentRoleLabel,
        },
    };
}

function upsertCurrentAiMessage(localMessages: Message[], currentAiMsg: Message) {
    const updatedAiMsg: Message = {
        ...currentAiMsg,
        nodes: [...currentAiMsg.nodes],
    };
    const lastIdx = localMessages.findIndex((message) => message.id === updatedAiMsg.id);
    if (lastIdx >= 0) {
        localMessages[lastIdx] = updatedAiMsg;
    }
    return updatedAiMsg;
}

export function applyRealtimeEventToMessages(
    event: RealtimeUiEvent,
    localMessages: Message[],
    currentAiMsg: Message | undefined,
    activeAgentProfile: AgentProfile,
) {
    let nextCurrentAiMsg = currentAiMsg;
    let nextActiveAgentProfile = activeAgentProfile;

    const ensureCurrentAiMsg = () => {
        if (!nextCurrentAiMsg) {
            const newMsg = buildAssistantMessage(nextActiveAgentProfile);
            localMessages.push(newMsg);
            nextCurrentAiMsg = newMsg;
        }
        if (!nextCurrentAiMsg.nodes) {
            nextCurrentAiMsg.nodes = [];
        }
        if (event.run_id && nextCurrentAiMsg) {
            nextCurrentAiMsg.runId = event.run_id;
        }
    };

    if (event.type === 'agent_start') {
        nextActiveAgentProfile = {
            agentName: event.agent?.name,
            agentAvatar: resolveAgentAvatar(event.agent?.avatar) || DEFAULT_AVATAR,
            agentRoleLabel: event.agent?.roleLabel,
        };
        ensureCurrentAiMsg();

        if (nextActiveAgentProfile.agentName === '智能主管' || nextActiveAgentProfile.agentRoleLabel === '主理人') {
            nextCurrentAiMsg!.agentName = nextActiveAgentProfile.agentName;
            nextCurrentAiMsg!.agentAvatar = nextActiveAgentProfile.agentAvatar;
            nextCurrentAiMsg!.agentRoleLabel = nextActiveAgentProfile.agentRoleLabel;
        }

        nextCurrentAiMsg!.nodes.push({
            id: createClientId('node'),
            kind: 'execution',
            executionType: 'agent_start',
            timestamp: Date.now(),
            ...nextActiveAgentProfile,
        } as UiExecutionNode);
    } else if (event.type === 'text_chunk') {
        ensureCurrentAiMsg();

        const isSupervisor = (
            nextActiveAgentProfile.agentName === '智能主管'
            || nextActiveAgentProfile.agentRoleLabel === '主理人'
        );
        const lastNode = nextCurrentAiMsg!.nodes[nextCurrentAiMsg!.nodes.length - 1] as UiNarrativeNode | undefined;
        if (lastNode && lastNode.kind === 'narrative' && lastNode.agentName === nextActiveAgentProfile.agentName) {
            lastNode.content = `${lastNode.content || ''}${event.content || ''}`;
        } else {
            nextCurrentAiMsg!.nodes.push({
                id: createClientId('node'),
                kind: 'narrative',
                role: 'assistant',
                content: event.content || '',
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } as UiNarrativeNode);
        }

        if (isSupervisor) {
            nextCurrentAiMsg!.content += event.content || '';
        }
    } else if (event.type === 'reasoning_chunk') {
        ensureCurrentAiMsg();
        const lastNode = nextCurrentAiMsg!.nodes[nextCurrentAiMsg!.nodes.length - 1] as UiExecutionNode | undefined;
        if (
            lastNode
            && lastNode.kind === 'execution'
            && lastNode.executionType === 'reasoning'
            && lastNode.agentName === nextActiveAgentProfile.agentName
            && !lastNode.time
        ) {
            lastNode.content = `${lastNode.content || ''}${event.content || ''}`;
        } else {
            nextCurrentAiMsg!.nodes.push({
                id: createClientId('node'),
                kind: 'execution',
                executionType: 'reasoning',
                content: event.content || '',
                time: 0,
                startTime: Date.now(),
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } as UiExecutionNode);
        }
    } else if (event.type === 'tool_start') {
        ensureCurrentAiMsg();
        nextCurrentAiMsg!.nodes.push({
            id: createClientId('node'),
            kind: 'execution',
            executionType: 'tool_call',
            toolCallId: event.tool?.toolCallId,
            toolName: event.tool?.toolName,
            args: event.tool?.args,
            timestamp: Date.now(),
            ...nextActiveAgentProfile,
        } as UiExecutionNode);
    } else if (event.type === 'tool_result') {
        ensureCurrentAiMsg();
        // Try to update existing tool_call node if present
        const toolCallNode = nextCurrentAiMsg!.nodes.find(n => n.kind === 'execution' && n.executionType === 'tool_call' && n.toolCallId === event.tool?.toolCallId) as UiExecutionNode | undefined;
        
        if (toolCallNode) {
            toolCallNode.result = event.tool?.result;
        } else {
            nextCurrentAiMsg!.nodes.push({
                id: createClientId('node'),
                kind: 'execution',
                executionType: 'tool_result',
                toolCallId: event.tool?.toolCallId,
                result: event.tool?.result,
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } as UiExecutionNode);
        }
    } else if (event.type === 'custom_event' && event.name === 'artifact_recorded') {
        ensureCurrentAiMsg();
        const normalizedArtifact = event.artifact || normalizeRuntimeArtifact(event.data?.artifact);
        if (normalizedArtifact) {
            const existingArtifacts = nextCurrentAiMsg!.artifacts || [];
            if (!existingArtifacts.some((artifact) => artifact.id === normalizedArtifact.id)) {
                nextCurrentAiMsg!.artifacts = [...existingArtifacts, normalizedArtifact];
                nextCurrentAiMsg!.nodes.push({
                    id: createClientId('node'),
                    kind: 'artifact',
                    artifact: normalizedArtifact,
                    timestamp: Date.now(),
                    ...nextActiveAgentProfile,
                } as UiArtifactNode);
            }
        }
    } else if (event.type === 'custom_event' && event.name === 'runtime_progress') {
        ensureCurrentAiMsg();
        const label = typeof event.data?.label === 'string' ? event.data.label : '';
        if (label) {
            const topic = typeof event.data?.topic === 'string' ? event.data.topic : 'runtime';
            const lastNode = nextCurrentAiMsg!.nodes[nextCurrentAiMsg!.nodes.length - 1] as UiExecutionNode | undefined;
            const canCoalesce =
                topic === 'computer_use.step.heartbeat'
                || topic === 'computer_use.step.waiting_for_window'
                || topic === 'computer_use.action.settle_wait_started';
                
            if (lastNode && lastNode.kind === 'execution' && lastNode.executionType === 'runtime_progress' && lastNode.label === label) {
                // no-op
            } else if (
                canCoalesce
                && lastNode
                && lastNode.kind === 'execution'
                && lastNode.executionType === 'runtime_progress'
                && lastNode.topic === topic
            ) {
                lastNode.label = label;
                lastNode.data = event.data;
            } else {
                nextCurrentAiMsg!.nodes.push({
                    id: createClientId('node'),
                    kind: 'execution',
                    executionType: 'runtime_progress',
                    topic,
                    label,
                    data: event.data,
                    timestamp: Date.now(),
                    ...nextActiveAgentProfile,
                } as UiExecutionNode);
            }
        }
    } else if (event.type === 'custom_event' && event.name === 'ask_user') {
        ensureCurrentAiMsg();
        nextCurrentAiMsg!.nodes.push({
            id: createClientId('node'),
            kind: 'governance',
            governanceType: 'approval_request',
            approvalId: event.data?.approvalId as string | undefined,
            approvalKind: event.data?.approvalKind as string | undefined,
            question: event.data?.question as string | undefined,
            toolCallId: event.data?.toolCallId as string | undefined,
            requestInfo: event.data?.request,
            timestamp: Date.now(),
            ...nextActiveAgentProfile
        } as UiGovernanceNode);
    } else if (event.type === 'custom_event' && event.name === 'run_controlled') {
        ensureCurrentAiMsg();
        nextCurrentAiMsg!.nodes.push({
            id: createClientId('node'),
            kind: 'governance',
            governanceType: 'run_controlled',
            topic: event.data?.topic as string | undefined,
            status: event.data?.status as string | undefined,
            reason: event.data?.reason as string | undefined,
            timestamp: Date.now(),
            ...nextActiveAgentProfile
        } as UiGovernanceNode);
    } else if (event.type === 'done' || event.type === 'error') {
        nextCurrentAiMsg = undefined;
    }

    if (nextCurrentAiMsg) {
        nextCurrentAiMsg = upsertCurrentAiMessage(localMessages, nextCurrentAiMsg);
    }

    return {
        currentAiMsg: nextCurrentAiMsg,
        activeAgentProfile: nextActiveAgentProfile,
    };
}

export function convertLegacyMessagesToChatMessages(rawMessages: Array<{
    id: string;
    role: 'user' | 'assistant' | 'system' | 'tool';
    content: string;
    reasoningContent?: string;
    agentName?: string;
    agentAvatar?: string;
    agentRoleLabel?: string;
    agentId?: string;
    createdAt: string;
    images?: string[];
    metadata?: Record<string, unknown>;
    toolInvocations?: Array<{
        toolCallId: string;
        toolName: string;
        args: Record<string, unknown>;
        result?: unknown;
    }>;
}>): Message[] {
    const formattedMessages: Message[] = [];
    let currentMergedMsg: Message | null = null;

    for (const msg of rawMessages) {
        const isAssistant = msg.role === 'assistant';
        const isTool = msg.role === 'tool';

        if (isTool && currentMergedMsg) {
            continue;
        }

        if (isAssistant && currentMergedMsg) {
            const prevAgentNode = currentMergedMsg.nodes.length > 0
                ? currentMergedMsg.nodes[currentMergedMsg.nodes.length - 1]
                : undefined;
            const prevAgentName = prevAgentNode?.agentName || currentMergedMsg.agentName;
            const thisAgentName = msg.agentName || '智能主管';

            if (prevAgentName && thisAgentName !== prevAgentName) {
                currentMergedMsg.nodes.push({
                    id: createClientId('node'),
                    kind: 'execution',
                    executionType: 'agent_start',
                    agentName: thisAgentName,
                    agentAvatar: resolveAgentAvatar(msg.agentAvatar),
                    agentRoleLabel: msg.agentRoleLabel,
                    timestamp: new Date(msg.createdAt).getTime(),
                } as UiExecutionNode);
            }

            if (msg.reasoningContent) {
                currentMergedMsg.nodes.push({
                    id: createClientId('node'),
                    kind: 'execution',
                    executionType: 'reasoning',
                    content: msg.reasoningContent,
                    agentName: thisAgentName,
                    agentAvatar: resolveAgentAvatar(msg.agentAvatar),
                    agentRoleLabel: msg.agentRoleLabel,
                    timestamp: new Date(msg.createdAt).getTime(),
                } as UiExecutionNode);
            }
            if (msg.content) {
                currentMergedMsg.content = currentMergedMsg.content
                    ? `${currentMergedMsg.content}\n\n${msg.content}`
                    : msg.content;
                currentMergedMsg.nodes.push({
                    id: createClientId('node'),
                    kind: 'narrative',
                    role: 'assistant',
                    content: msg.content,
                    agentName: thisAgentName,
                    agentAvatar: resolveAgentAvatar(msg.agentAvatar),
                    agentRoleLabel: msg.agentRoleLabel,
                    timestamp: new Date(msg.createdAt).getTime(),
                } as UiNarrativeNode);
            }
            if (msg.toolInvocations && Array.isArray(msg.toolInvocations)) {
                msg.toolInvocations.forEach((tool) => {
                    currentMergedMsg!.nodes.push({
                        id: createClientId('node'),
                        kind: 'execution',
                        executionType: 'tool_call',
                        toolCallId: tool.toolCallId,
                        toolName: tool.toolName,
                        args: tool.args,
                        result: tool.result,
                        agentName: thisAgentName,
                        agentAvatar: resolveAgentAvatar(msg.agentAvatar),
                        agentRoleLabel: msg.agentRoleLabel,
                        timestamp: new Date(msg.createdAt).getTime(),
                    } as UiExecutionNode);
                });
            }
            if (msg.images && msg.images.length > 0) {
                currentMergedMsg.images = [...(currentMergedMsg.images || []), ...msg.images];
            }
            continue;
        }

        if (currentMergedMsg) {
            formattedMessages.push(currentMergedMsg);
            currentMergedMsg = null;
        }

        if (isAssistant) {
            const agentName = msg.agentName || '智能主管';
            const nodes: UiTimelineNode[] = [];
            if (msg.reasoningContent) {
                nodes.push({
                    id: createClientId('node'),
                    kind: 'execution',
                    executionType: 'reasoning',
                    content: msg.reasoningContent,
                    agentName,
                    agentAvatar: msg.agentAvatar,
                    agentRoleLabel: msg.agentRoleLabel,
                    timestamp: new Date(msg.createdAt).getTime(),
                } as UiExecutionNode);
            }
            if (msg.content) {
                nodes.push({
                    id: createClientId('node'),
                    kind: 'narrative',
                    role: 'assistant',
                    content: msg.content,
                    agentName,
                    agentAvatar: msg.agentAvatar,
                    agentRoleLabel: msg.agentRoleLabel,
                    timestamp: new Date(msg.createdAt).getTime(),
                } as UiNarrativeNode);
            }
            if (msg.toolInvocations && Array.isArray(msg.toolInvocations)) {
                msg.toolInvocations.forEach((tool) => {
                    nodes.push({
                        id: createClientId('node'),
                        kind: 'execution',
                        executionType: 'tool_call',
                        toolCallId: tool.toolCallId,
                        toolName: tool.toolName,
                        args: tool.args,
                        result: tool.result,
                        agentName,
                        agentAvatar: msg.agentAvatar,
                        agentRoleLabel: msg.agentRoleLabel,
                        timestamp: new Date(msg.createdAt).getTime(),
                    } as UiExecutionNode);
                });
            }

            currentMergedMsg = {
                id: msg.id,
                role: 'assistant',
                runId: undefined,
                content: msg.content || '',
                nodes,
                agentName,
                agentAvatar: resolveAgentAvatar(msg.agentAvatar),
                agentRoleLabel: msg.agentRoleLabel,
                agentType: msg.agentId === 'SYSTEM_SUPERVISOR' || msg.agentName === '智能主管' ? 'supervisor' : (msg.agentId ? 'agent' : 'user'),
                timestamp: new Date(msg.createdAt).getTime(),
                images: msg.images || [],
                artifacts: [],
                metadata: msg.metadata && typeof msg.metadata === 'object' ? { ...msg.metadata } : undefined,
            };
        } else {
            const nodes: UiTimelineNode[] = [];
            if (msg.content) {
                nodes.push({ id: createClientId('node'), kind: 'narrative', role: 'user', content: msg.content, timestamp: new Date(msg.createdAt).getTime() } as UiNarrativeNode);
            }
            formattedMessages.push({
                id: msg.id,
                role: msg.role,
                runId: undefined,
                content: msg.content || '',
                nodes,
                agentName: msg.agentName,
                agentAvatar: resolveAgentAvatar(msg.agentAvatar),
                agentRoleLabel: msg.agentRoleLabel,
                agentType: 'user',
                timestamp: new Date(msg.createdAt).getTime(),
                images: msg.images || [],
                artifacts: [],
                metadata: msg.metadata && typeof msg.metadata === 'object' ? { ...msg.metadata } : undefined,
            });
        }
    }

    if (currentMergedMsg) {
        formattedMessages.push(currentMergedMsg);
    }

    return normalizeMessagesForState(formattedMessages);
}
