import type { AskUserInteraction, ChatArtifact, PendingApproval } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import {
    type SessionStreamUiEvent,
    buildSessionStreamUiEvent,
    coerceAdminResourceRef,
} from "@v8/session-realtime";

type JsonRecord = Record<string, unknown>;

export type PhoneRealtimeEvent = SessionStreamUiEvent & {
    runtimeId?: string;
    event_id?: string;
    ts?: string;
    actorLabel?: string;
    session_id?: string;
    conversation_id?: string;
    run_id?: string;
    artifact?: ChatArtifact;
};


function buildArtifact(value: unknown): ChatArtifact | null {
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as JsonRecord;
    return {
        id: typeof record.id === "string" ? record.id : undefined,
        artifactId: typeof record.artifactId === "string"
            ? record.artifactId
            : typeof record.artifact_id === "string"
                ? record.artifact_id
                : undefined,
        title: typeof record.title === "string" ? record.title : undefined,
        kind: typeof record.kind === "string" ? record.kind : undefined,
        previewBlockedReason: typeof record.previewBlockedReason === "string"
            ? record.previewBlockedReason
            : typeof record.preview_blocked_reason === "string"
                ? record.preview_blocked_reason
                : undefined,
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
}

export function normalizePhoneRealtimeEvent(raw: unknown, locale: LocaleCode = "zh-CN"): PhoneRealtimeEvent | null {
    return buildSessionStreamUiEvent(raw, {
        locale,
        artifactResolver: (artifact, event) => buildArtifact(artifact || event.artifact || event.data?.artifact || event.data),
    }) as PhoneRealtimeEvent | null;
}

export function buildApprovalFromEvent(event: PhoneRealtimeEvent): PendingApproval | null {
    if (event.type !== "custom_event" || event.name !== "approval_requested") {
        return null;
    }
    const approvalId = typeof event.data?.approvalId === "string"
        ? event.data.approvalId
        : typeof event.data?.approval_id === "string"
            ? event.data.approval_id
            : undefined;
    const approvalKind = typeof event.data?.approvalKind === "string"
        ? event.data.approvalKind
        : typeof event.data?.approval_kind === "string"
            ? event.data.approval_kind
            : undefined;
    const interactionKind = typeof event.data?.interactionKind === "string"
        ? event.data.interactionKind
        : typeof event.data?.interaction_kind === "string"
            ? event.data.interaction_kind
            : undefined;
    const request = {
        ...(event.data?.request && typeof event.data.request === "object" ? event.data.request as Record<string, unknown> : {}),
        question: typeof event.data?.question === "string" ? event.data.question : undefined,
        prompt: typeof event.data?.prompt === "string" ? event.data.prompt : undefined,
        toolCallId: typeof event.data?.toolCallId === "string" ? event.data.toolCallId : undefined,
        interactionKind,
    };
    const candidate: PendingApproval = {
        id: approvalId,
        approval_id: approvalId,
        run_id: typeof event.run_id === "string"
            ? event.run_id
            : typeof event.data?.run_id === "string"
                ? event.data.run_id
                : undefined,
        session_id: typeof event.session_id === "string"
            ? event.session_id
            : typeof event.data?.session_id === "string"
                ? event.data.session_id
                : undefined,
        approval_kind: approvalKind,
        request,
    };
    return {
        ...candidate,
        request,
    };
}

export function buildAskUserInteractionFromEvent(event: PhoneRealtimeEvent): AskUserInteraction | null {
    if (event.type !== "custom_event" || event.name !== "ask_user") {
        return null;
    }
    const data = event.data || {};
    const request = {
        ...(data.request && typeof data.request === "object" ? data.request as Record<string, unknown> : {}),
        question: typeof data.question === "string" ? data.question : undefined,
        prompt: typeof data.prompt === "string" ? data.prompt : undefined,
        toolCallId: typeof data.toolCallId === "string" ? data.toolCallId : undefined,
        interactionKind: "ask_user",
    };
    const interactionId = typeof data.interactionId === "string"
        ? data.interactionId
        : typeof data.id === "string"
            ? data.id
            : undefined;
    return {
        id: interactionId,
        interactionId,
        run_id: typeof event.run_id === "string"
            ? event.run_id
            : typeof data.run_id === "string"
                ? data.run_id
                : undefined,
        session_id: typeof event.session_id === "string"
            ? event.session_id
            : typeof data.session_id === "string"
                ? data.session_id
                : undefined,
        assistantMessageId: typeof data.assistantMessageId === "string" ? data.assistantMessageId : undefined,
        toolCallId: typeof data.toolCallId === "string" ? data.toolCallId : undefined,
        status: typeof data.status === "string" ? data.status : "pending",
        question: typeof data.question === "string" ? data.question : request.question,
        prompt: typeof data.prompt === "string" ? data.prompt : request.prompt,
        request,
    };
}
