import type {
    ChatArtifact,
    ChatMessage,
} from "@/src/types/admin";
import {
    applyRealtimeEventToMessages as applySharedRealtimeEventToMessages,
    buildAssistantMessage as buildSharedAssistantMessage,
    coerceAdminResourceRef,
    deriveRealtimeStreamState as deriveSharedRealtimeStreamState,
    type SessionAgentProfile,
    type SessionStreamLifecycleOptions,
    type SessionStreamMessage,
    type SessionStreamPhase,
    type SessionStreamUiEvent,
} from "@v8/session-realtime";

export type PhoneUiStreamPhase =
    | SessionStreamPhase
    | "task_planning"
    | "tooling"
    | "artifact_ready"
    | "waiting_input";

export type AgentProfile = SessionAgentProfile;

export type PhoneRealtimeUiEvent = SessionStreamUiEvent & {
    artifact?: ChatArtifact;
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
        workspaceRoot: typeof record.workspaceRoot === "string"
            ? record.workspaceRoot
            : typeof record.workspace_root === "string"
                ? record.workspace_root
                : undefined,
        workspaceRelativePath: typeof record.workspaceRelativePath === "string"
            ? record.workspaceRelativePath
            : typeof record.workspace_relative_path === "string"
                ? record.workspace_relative_path
                : undefined,
        canonicalPath: typeof record.canonicalPath === "string"
            ? record.canonicalPath
            : typeof record.canonical_path === "string"
                ? record.canonical_path
                : undefined,
        projectId: typeof record.projectId === "string"
            ? record.projectId
            : typeof record.project_id === "string"
                ? record.project_id
                : undefined,
        workspaceId: typeof record.workspaceId === "string"
            ? record.workspaceId
            : typeof record.workspace_id === "string"
                ? record.workspace_id
                : undefined,
        storageClass: typeof record.storageClass === "string"
            ? record.storageClass
            : typeof record.storage_class === "string"
                ? record.storage_class
                : undefined,
        surfaceVisible: typeof record.surfaceVisible === "boolean"
            ? record.surfaceVisible
            : typeof record.surface_visible === "boolean"
                ? record.surface_visible
                : undefined,
        mimeType: typeof record.mimeType === "string"
            ? record.mimeType
            : typeof record.mime_type === "string"
                ? record.mime_type
                : undefined,
        resourceRef: coerceAdminResourceRef(record.resourceRef || record.resource_ref || null),
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

export const PHONE_STREAM_LIFECYCLE_OPTIONS: SessionStreamLifecycleOptions = {
    createId: createClientId,
    defaultAgentProfile: DEFAULT_AGENT_PROFILE,
    resolveAgentProfile: (event, fallback, defaultAgentProfile) => {
        const resolvedFallback = resolveAgentProfile(
            event as PhoneRealtimeUiEvent,
            {
                agentName: fallback.agentName || defaultAgentProfile.agentName,
                agentAvatar: fallback.agentAvatar || defaultAgentProfile.agentAvatar,
                agentRoleLabel: fallback.agentRoleLabel || defaultAgentProfile.agentRoleLabel,
            },
        );
        return {
            agentName: resolvedFallback.agentName || defaultAgentProfile.agentName,
            agentAvatar: resolvedFallback.agentAvatar || defaultAgentProfile.agentAvatar,
            agentRoleLabel: resolvedFallback.agentRoleLabel || defaultAgentProfile.agentRoleLabel,
        };
    },
    resolveArtifact: (event) => buildArtifact(event.artifact || resolveRecord(event.data).artifact || event.data),
};

export function isActiveAssistantStreamPhase(phase?: PhoneUiStreamPhase | null) {
    return phase === "placeholder"
        || phase === "agent_started"
        || phase === "task_planning"
        || phase === "tooling"
        || phase === "artifact_ready"
        || phase === "waiting_input"
        || phase === "streaming"
        || phase === "settling";
}

export function buildAssistantMessage(
    activeAgentProfile: AgentProfile,
    runId?: string,
    phase: PhoneUiStreamPhase = "placeholder",
): ChatMessage {
    return buildSharedAssistantMessage(activeAgentProfile, runId, phase, PHONE_STREAM_LIFECYCLE_OPTIONS) as ChatMessage;
}

export function deriveRealtimeStreamState(messages: ChatMessage[]) {
    const state = deriveSharedRealtimeStreamState(messages as unknown as SessionStreamMessage[], PHONE_STREAM_LIFECYCLE_OPTIONS);
    return {
        currentAiMsg: state.currentAiMsg as ChatMessage | undefined,
        activeAgentProfile: {
            agentName: state.activeAgentProfile.agentName || DEFAULT_AGENT_PROFILE.agentName,
            agentAvatar: state.activeAgentProfile.agentAvatar || DEFAULT_AGENT_PROFILE.agentAvatar,
            agentRoleLabel: state.activeAgentProfile.agentRoleLabel || DEFAULT_AGENT_PROFILE.agentRoleLabel,
        } satisfies AgentProfile,
    };
}

export function applyRealtimeEventToMessages(
    event: PhoneRealtimeUiEvent,
    localMessages: ChatMessage[],
    currentAiMsg: ChatMessage | undefined,
    activeAgentProfile: AgentProfile,
) {
    const result = applySharedRealtimeEventToMessages(
        event,
        localMessages as unknown as SessionStreamMessage[],
        currentAiMsg as unknown as SessionStreamMessage | undefined,
        activeAgentProfile,
        PHONE_STREAM_LIFECYCLE_OPTIONS,
    );
    return {
        currentAiMsg: result.currentAiMsg as ChatMessage | undefined,
        activeAgentProfile: {
            agentName: result.activeAgentProfile.agentName || DEFAULT_AGENT_PROFILE.agentName,
            agentAvatar: result.activeAgentProfile.agentAvatar || DEFAULT_AGENT_PROFILE.agentAvatar,
            agentRoleLabel: result.activeAgentProfile.agentRoleLabel || DEFAULT_AGENT_PROFILE.agentRoleLabel,
        } satisfies AgentProfile,
    };
}
