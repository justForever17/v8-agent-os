import type {
    ChatArtifact,
    ChatMessage,
    PhoneUiArtifactNode,
    PhoneUiExecutionNode,
    PhoneUiGovernanceNode,
    PhoneUiNarrativeNode,
    PhoneUiTimelineNode,
} from "@/src/types/admin";

export type PhoneUiStreamPhase = "placeholder" | "agent_started" | "streaming" | "settling" | "error";

export type AgentProfile = {
    agentName?: string;
    agentAvatar?: string;
    agentRoleLabel?: string;
};

export type PhoneRealtimeUiEvent = {
    type: string;
    name?: string;
    content?: string;
    data?: Record<string, unknown>;
    run_id?: string;
    error?: string;
    artifact?: ChatArtifact;
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
};

const DEFAULT_AGENT_PROFILE: Required<AgentProfile> = {
    agentName: "智能主管",
    agentAvatar: "/brand-mark.png",
    agentRoleLabel: "主理人",
};

function createClientId(prefix: string) {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function resolveRecord(value: unknown) {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function buildArtifact(value: unknown): ChatArtifact | null {
    const record = resolveRecord(value);
    const candidate: ChatArtifact = {
        id: typeof record.id === "string" ? record.id : undefined,
        artifactId: typeof record.artifactId === "string"
            ? record.artifactId
            : typeof record.artifact_id === "string"
                ? record.artifact_id
                : undefined,
        title: typeof record.title === "string" ? record.title : undefined,
        kind: typeof record.kind === "string" ? record.kind : undefined,
        previewUrl: typeof record.previewUrl === "string"
            ? record.previewUrl
            : typeof record.preview_url === "string"
                ? record.preview_url
                : undefined,
        externalUrl: typeof record.externalUrl === "string"
            ? record.externalUrl
            : typeof record.external_url === "string"
                ? record.external_url
                : undefined,
        sourcePath: typeof record.sourcePath === "string"
            ? record.sourcePath
            : typeof record.source_path === "string"
                ? record.source_path
                : undefined,
        workspacePath: typeof record.workspacePath === "string"
            ? record.workspacePath
            : typeof record.workspace_path === "string"
                ? record.workspace_path
                : undefined,
        mimeType: typeof record.mimeType === "string"
            ? record.mimeType
            : typeof record.mime_type === "string"
                ? record.mime_type
                : undefined,
    };

    if (
        !candidate.id
        && !candidate.artifactId
        && !candidate.previewUrl
        && !candidate.externalUrl
        && !candidate.workspacePath
        && !candidate.sourcePath
        && !candidate.title
    ) {
        return null;
    }

    return candidate;
}

function resolveAgentProfile(event: PhoneRealtimeUiEvent, fallback: AgentProfile): AgentProfile {
    const eventData = resolveRecord(event.data);
    const agentData = resolveRecord(eventData.agent);
    const roleLabel = event.agent?.roleLabel
        || (typeof eventData.agentRoleLabel === "string" ? eventData.agentRoleLabel : "")
        || (typeof agentData.roleLabel === "string" ? agentData.roleLabel : "")
        || fallback.agentRoleLabel;
    const agentName = event.agent?.name
        || (typeof eventData.agentName === "string" ? eventData.agentName : "")
        || (typeof agentData.name === "string" ? agentData.name : "")
        || fallback.agentName;
    const agentAvatar = event.agent?.avatar
        || (typeof eventData.agentAvatar === "string" ? eventData.agentAvatar : "")
        || (typeof agentData.avatar === "string" ? agentData.avatar : "")
        || fallback.agentAvatar;

    return {
        agentName: agentName || DEFAULT_AGENT_PROFILE.agentName,
        agentAvatar: agentAvatar || DEFAULT_AGENT_PROFILE.agentAvatar,
        agentRoleLabel: roleLabel || DEFAULT_AGENT_PROFILE.agentRoleLabel,
    };
}

function ensureAssistantIdentity(message: ChatMessage, profile: AgentProfile) {
    message.agentName = profile.agentName || message.agentName || DEFAULT_AGENT_PROFILE.agentName;
    message.agentAvatar = profile.agentAvatar || message.agentAvatar || DEFAULT_AGENT_PROFILE.agentAvatar;
    message.agentRoleLabel = profile.agentRoleLabel || message.agentRoleLabel || DEFAULT_AGENT_PROFILE.agentRoleLabel;
    message.agentType = message.agentType || "supervisor";
}

function upsertCurrentAiMessage(localMessages: ChatMessage[], currentAiMsg: ChatMessage) {
    const updatedAiMsg: ChatMessage = {
        ...currentAiMsg,
        nodes: Array.isArray(currentAiMsg.nodes) ? [...currentAiMsg.nodes] : [],
        images: Array.isArray(currentAiMsg.images) ? [...currentAiMsg.images] : [],
        artifacts: Array.isArray(currentAiMsg.artifacts) ? currentAiMsg.artifacts.map((artifact) => ({ ...artifact })) : [],
        metadata: currentAiMsg.metadata ? { ...currentAiMsg.metadata } : undefined,
    };
    const index = localMessages.findIndex((message) => message.id === updatedAiMsg.id);
    if (index >= 0) {
        localMessages[index] = updatedAiMsg;
    } else {
        localMessages.push(updatedAiMsg);
    }
    return updatedAiMsg;
}

function ensureCurrentAiMessage(
    localMessages: ChatMessage[],
    currentAiMsg: ChatMessage | undefined,
    activeAgentProfile: AgentProfile,
    runId?: string,
) {
    let nextCurrentAiMsg = currentAiMsg;
    if (!nextCurrentAiMsg) {
        nextCurrentAiMsg = buildAssistantMessage(activeAgentProfile, runId, "placeholder");
        localMessages.push(nextCurrentAiMsg);
    }
    if (!Array.isArray(nextCurrentAiMsg.nodes)) {
        nextCurrentAiMsg.nodes = [];
    }
    if (!Array.isArray(nextCurrentAiMsg.images)) {
        nextCurrentAiMsg.images = [];
    }
    if (!Array.isArray(nextCurrentAiMsg.artifacts)) {
        nextCurrentAiMsg.artifacts = [];
    }
    if (runId) {
        nextCurrentAiMsg.runId = runId;
    }
    ensureAssistantIdentity(nextCurrentAiMsg, activeAgentProfile);
    return nextCurrentAiMsg;
}

function appendNode(message: ChatMessage, node: PhoneUiTimelineNode) {
    const nextNodes = Array.isArray(message.nodes) ? message.nodes : [];
    nextNodes.push(node);
    message.nodes = nextNodes;
    message.timestamp = Date.now();
}

export function isActiveAssistantStreamPhase(phase?: PhoneUiStreamPhase | null) {
    return phase === "placeholder" || phase === "agent_started" || phase === "streaming" || phase === "settling";
}

export function buildAssistantMessage(
    activeAgentProfile: AgentProfile,
    runId?: string,
    phase: PhoneUiStreamPhase = "placeholder",
): ChatMessage {
    const resolvedProfile = {
        ...DEFAULT_AGENT_PROFILE,
        ...activeAgentProfile,
    };
    return {
        id: createClientId("assistant"),
        role: "assistant",
        content: "",
        runId,
        nodes: [],
        images: [],
        artifacts: [],
        agentName: resolvedProfile.agentName,
        agentAvatar: resolvedProfile.agentAvatar,
        agentRoleLabel: resolvedProfile.agentRoleLabel,
        agentType: "supervisor",
        timestamp: Date.now(),
        uiEphemeral: true,
        uiStreamPhase: phase,
    };
}

export function deriveRealtimeStreamState(messages: ChatMessage[]) {
    const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");
    if (!lastAssistant) {
        return {
            currentAiMsg: undefined,
            activeAgentProfile: { ...DEFAULT_AGENT_PROFILE } satisfies AgentProfile,
        };
    }

    const nodes = Array.isArray(lastAssistant.nodes) ? lastAssistant.nodes : [];
    const lastAgentNode = [...nodes].reverse().find((node) => node.agentName || node.agentAvatar || node.agentRoleLabel);

    const currentAiMsg = lastAssistant.uiEphemeral || isActiveAssistantStreamPhase(lastAssistant.uiStreamPhase)
        ? lastAssistant
        : undefined;

    return {
        currentAiMsg,
        activeAgentProfile: {
            agentName: lastAgentNode?.agentName || lastAssistant.agentName || DEFAULT_AGENT_PROFILE.agentName,
            agentAvatar: lastAgentNode?.agentAvatar || lastAssistant.agentAvatar || DEFAULT_AGENT_PROFILE.agentAvatar,
            agentRoleLabel: lastAgentNode?.agentRoleLabel || lastAssistant.agentRoleLabel || DEFAULT_AGENT_PROFILE.agentRoleLabel,
        } satisfies AgentProfile,
    };
}

export function applyRealtimeEventToMessages(
    event: PhoneRealtimeUiEvent,
    localMessages: ChatMessage[],
    currentAiMsg: ChatMessage | undefined,
    activeAgentProfile: AgentProfile,
) {
    let nextCurrentAiMsg = currentAiMsg;
    let nextActiveAgentProfile = activeAgentProfile;

    const ensureCurrent = () => {
        nextCurrentAiMsg = ensureCurrentAiMessage(
            localMessages,
            nextCurrentAiMsg,
            nextActiveAgentProfile,
            event.run_id,
        );
        return nextCurrentAiMsg;
    };

    if (event.type === "agent_start") {
        nextActiveAgentProfile = resolveAgentProfile(event, nextActiveAgentProfile);
        const current = ensureCurrent();
        current.uiStreamPhase = "agent_started";
        ensureAssistantIdentity(current, nextActiveAgentProfile);
        appendNode(current, {
            id: createClientId("node"),
            kind: "execution",
            executionType: "agent_start",
            timestamp: Date.now(),
            ...nextActiveAgentProfile,
        } satisfies PhoneUiExecutionNode);
    } else if (event.type === "text_chunk") {
        const current = ensureCurrent();
        current.uiStreamPhase = "streaming";
        const content = String(event.content || "");
        const lastNode = Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined;
        if (lastNode && lastNode.kind === "narrative" && lastNode.role === "assistant") {
            (lastNode as PhoneUiNarrativeNode).content = `${String(lastNode.content || "")}${content}`;
        } else {
            appendNode(current, {
                id: createClientId("node"),
                kind: "narrative",
                role: "assistant",
                content,
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } satisfies PhoneUiNarrativeNode);
        }
        current.content = `${String(current.content || "")}${content}`;
    } else if (event.type === "reasoning_chunk") {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const content = String(event.content || "");
        const lastNode = Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined;
        if (
            lastNode
            && lastNode.kind === "execution"
            && lastNode.executionType === "reasoning"
            && !lastNode.time
        ) {
            (lastNode as PhoneUiExecutionNode).content = `${String(lastNode.content || "")}${content}`;
        } else {
            appendNode(current, {
                id: createClientId("node"),
                kind: "execution",
                executionType: "reasoning",
                content,
                time: 0,
                startTime: Date.now(),
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } satisfies PhoneUiExecutionNode);
        }
    } else if (event.type === "tool_start") {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const eventData = resolveRecord(event.data);
        const toolData = resolveRecord(eventData.tool);
        appendNode(current, {
            id: createClientId("node"),
            kind: "execution",
            executionType: "tool_call",
            toolCallId: event.tool?.toolCallId || (typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined),
            toolName: event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : typeof toolData.name === "string" ? toolData.name : undefined),
            args: event.tool?.args ?? eventData.args ?? toolData.args,
            timestamp: Date.now(),
            ...nextActiveAgentProfile,
        } satisfies PhoneUiExecutionNode);
    } else if (event.type === "tool_result") {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const eventData = resolveRecord(event.data);
        const toolCallId = event.tool?.toolCallId || (typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined);
        const existingToolCall = (current.nodes || []).find((node) =>
            node.kind === "execution"
            && node.executionType === "tool_call"
            && node.toolCallId === toolCallId,
        ) as PhoneUiExecutionNode | undefined;
        if (existingToolCall) {
            existingToolCall.result = event.tool?.result ?? eventData.result;
            existingToolCall.timestamp = Date.now();
        } else {
            appendNode(current, {
                id: createClientId("node"),
                kind: "execution",
                executionType: "tool_result",
                toolCallId,
                toolName: event.tool?.toolName || (typeof eventData.toolName === "string" ? eventData.toolName : undefined),
                result: event.tool?.result ?? eventData.result,
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } satisfies PhoneUiExecutionNode);
        }
    } else if (event.type === "custom_event" && event.name === "artifact_recorded") {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const normalizedArtifact = event.artifact || buildArtifact(resolveRecord(event.data).artifact) || buildArtifact(event.data);
        if (normalizedArtifact) {
            const existingArtifacts = current.artifacts || [];
            const artifactKey = String(
                normalizedArtifact.id
                || normalizedArtifact.artifactId
                || normalizedArtifact.workspacePath
                || normalizedArtifact.sourcePath
                || normalizedArtifact.previewUrl
                || normalizedArtifact.externalUrl
                || normalizedArtifact.title
                || "",
            ).trim();
            if (!existingArtifacts.some((artifact) => String(
                artifact.id
                || artifact.artifactId
                || artifact.workspacePath
                || artifact.sourcePath
                || artifact.previewUrl
                || artifact.externalUrl
                || artifact.title
                || "",
            ).trim() === artifactKey)) {
                current.artifacts = [...existingArtifacts, normalizedArtifact];
                appendNode(current, {
                    id: createClientId("node"),
                    kind: "artifact",
                    artifact: {
                        id: String(
                            normalizedArtifact.id
                            || normalizedArtifact.artifactId
                            || normalizedArtifact.workspacePath
                            || normalizedArtifact.sourcePath
                            || normalizedArtifact.previewUrl
                            || normalizedArtifact.externalUrl
                            || createClientId("artifact"),
                        ).trim(),
                        artifactId: normalizedArtifact.artifactId,
                        title: normalizedArtifact.title,
                        kind: normalizedArtifact.kind,
                        previewUrl: normalizedArtifact.previewUrl,
                        externalUrl: normalizedArtifact.externalUrl,
                        sourcePath: normalizedArtifact.sourcePath,
                        workspacePath: normalizedArtifact.workspacePath,
                        mimeType: normalizedArtifact.mimeType,
                    },
                    timestamp: Date.now(),
                    ...nextActiveAgentProfile,
                } satisfies PhoneUiArtifactNode);
            }
        }
    } else if (event.type === "custom_event" && (event.name === "runtime_progress" || event.name === "runtime_event")) {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const eventData = resolveRecord(event.data);
        const topic = typeof eventData.topic === "string" ? eventData.topic : event.name === "runtime_event" ? "runtime" : "runtime_progress";
        const label = typeof eventData.label === "string"
            ? eventData.label
            : typeof eventData.summary === "string"
                ? eventData.summary
                : typeof eventData.message === "string"
                    ? eventData.message
                    : topic;
        const canCoalesce =
            topic === "computer_use.step.heartbeat"
            || topic === "computer_use.step.waiting_for_window"
            || topic === "computer_use.action.settle_wait_started";
        const lastNode = Array.isArray(current.nodes) ? current.nodes[current.nodes.length - 1] : undefined;
        if (lastNode && lastNode.kind === "execution" && lastNode.executionType === "runtime_progress" && lastNode.label === label) {
            (lastNode as PhoneUiExecutionNode).data = eventData;
            (lastNode as PhoneUiExecutionNode).timestamp = Date.now();
        } else if (
            canCoalesce
            && lastNode
            && lastNode.kind === "execution"
            && lastNode.executionType === "runtime_progress"
            && lastNode.topic === topic
        ) {
            (lastNode as PhoneUiExecutionNode).label = label;
            (lastNode as PhoneUiExecutionNode).data = eventData;
            (lastNode as PhoneUiExecutionNode).timestamp = Date.now();
        } else {
            appendNode(current, {
                id: createClientId("node"),
                kind: "execution",
                executionType: "runtime_progress",
                topic,
                label,
                data: eventData,
                timestamp: Date.now(),
                ...nextActiveAgentProfile,
            } satisfies PhoneUiExecutionNode);
        }
    } else if (event.type === "custom_event" && event.name === "ask_user") {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const eventData = resolveRecord(event.data);
        appendNode(current, {
            id: createClientId("node"),
            kind: "governance",
            governanceType: "approval_request",
            approvalId: typeof eventData.approvalId === "string" ? eventData.approvalId : undefined,
            approvalKind: typeof eventData.approvalKind === "string" ? eventData.approvalKind : undefined,
            question: typeof eventData.question === "string" ? eventData.question : undefined,
            toolCallId: typeof eventData.toolCallId === "string" ? eventData.toolCallId : undefined,
            requestInfo: eventData.request,
            timestamp: Date.now(),
            ...nextActiveAgentProfile,
        } satisfies PhoneUiGovernanceNode);
    } else if (event.type === "custom_event" && event.name === "run_controlled") {
        const current = ensureCurrent();
        current.uiStreamPhase = current.content ? "streaming" : "agent_started";
        const eventData = resolveRecord(event.data);
        appendNode(current, {
            id: createClientId("node"),
            kind: "governance",
            governanceType: "run_controlled",
            topic: typeof eventData.topic === "string" ? eventData.topic : undefined,
            status: typeof eventData.status === "string" ? eventData.status : undefined,
            reason: typeof eventData.reason === "string" ? eventData.reason : undefined,
            timestamp: Date.now(),
            ...nextActiveAgentProfile,
        } satisfies PhoneUiGovernanceNode);
    } else if (event.type === "done") {
        if (nextCurrentAiMsg) {
            nextCurrentAiMsg.uiStreamPhase = "settling";
            upsertCurrentAiMessage(localMessages, nextCurrentAiMsg);
        }
        nextCurrentAiMsg = undefined;
    } else if (event.type === "error") {
        if (nextCurrentAiMsg) {
            nextCurrentAiMsg.uiStreamPhase = "error";
            upsertCurrentAiMessage(localMessages, nextCurrentAiMsg);
        }
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
