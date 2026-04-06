import type { ChatArtifact, ChatMessage, ChatStreamEvent, PendingApproval } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import {
    type NormalizedSessionRuntimeEvent,
    buildSessionStreamUiEvent,
} from "@v8/session-realtime";

type JsonRecord = Record<string, unknown>;

export type PhoneRealtimeEvent = NormalizedSessionRuntimeEvent & {
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
}

export function normalizePhoneRealtimeEvent(raw: unknown, locale: LocaleCode = "zh-CN"): PhoneRealtimeEvent | null {
    return buildSessionStreamUiEvent(raw, {
        locale,
        artifactResolver: (artifact, event) => buildArtifact(artifact || event.artifact || event.data?.artifact || event.data),
    }) as PhoneRealtimeEvent | null;
}

export function buildApprovalFromEvent(event: PhoneRealtimeEvent): PendingApproval | null {
    if (event.type !== "custom_event" || event.name !== "ask_user") {
        return null;
    }
    return {
        id: typeof event.data?.approvalId === "string" ? event.data.approvalId : undefined,
        approval_id: typeof event.data?.approvalId === "string" ? event.data.approvalId : undefined,
        run_id: typeof event.run_id === "string" ? event.run_id : undefined,
        session_id: typeof event.session_id === "string" ? event.session_id : undefined,
        approval_kind: typeof event.data?.approvalKind === "string" ? event.data.approvalKind : undefined,
        request: {
            ...(event.data?.request && typeof event.data.request === "object" ? event.data.request as Record<string, unknown> : {}),
            question: typeof event.data?.question === "string" ? event.data.question : undefined,
            toolCallId: typeof event.data?.toolCallId === "string" ? event.data.toolCallId : undefined,
            interactionKind: typeof event.data?.interactionKind === "string" ? event.data.interactionKind : undefined,
        },
    };
}

export function collectArtifactsFromMessages(messages: ChatMessage[]) {
    const merged = new Map<string, ChatArtifact>();
    for (const message of messages) {
        for (const artifact of message.artifacts || []) {
            const key = String(
                artifact.id
                || artifact.artifactId
                || artifact.workspacePath
                || artifact.sourcePath
                || artifact.previewUrl
                || artifact.externalUrl
                || `${artifact.kind || "artifact"}:${artifact.title || ""}`,
            ).trim();
            if (!key) {
                continue;
            }
            merged.set(key, {
                ...(merged.get(key) || {}),
                ...artifact,
            });
        }
    }
    return Array.from(merged.values());
}
