import type { ChatArtifact, ChatMessage, ChatStreamEvent, PendingApproval } from "@/src/types/admin";

type JsonRecord = Record<string, unknown>;

type RuntimeEnvelope = JsonRecord & {
    kind?: string;
    topic?: string;
    payload?: unknown;
};

export type PhoneRealtimeEvent = ChatStreamEvent & {
    seq?: number;
    session_id?: string;
    conversation_id?: string;
    event_id?: string;
    ts?: string;
    topic?: string;
    artifact?: ChatArtifact;
};

function withEnvelopeFields(event: PhoneRealtimeEvent, envelope: RuntimeEnvelope): PhoneRealtimeEvent {
    return {
        ...event,
        seq: typeof envelope.seq === "number" ? envelope.seq : undefined,
        session_id: typeof envelope.session_id === "string" ? envelope.session_id : undefined,
        conversation_id: typeof envelope.conversation_id === "string" ? envelope.conversation_id : undefined,
        run_id: typeof envelope.run_id === "string" ? envelope.run_id : event.run_id,
        event_id: typeof envelope.event_id === "string" ? envelope.event_id : undefined,
        ts: typeof envelope.ts === "string" ? envelope.ts : undefined,
        topic: typeof envelope.topic === "string" ? envelope.topic : undefined,
    };
}

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

function buildProgressLabel(topic: string, payload: JsonRecord) {
    const action = String(payload.action || payload.actionType || payload.appName || payload.expectedTitle || "").trim();
    const index = typeof payload.index === "number" ? payload.index : undefined;
    if (typeof payload.label === "string" && payload.label.trim()) {
        return payload.label.trim();
    }
    if (topic === "computer_use.step.started") {
        return `Desktop Live 第 ${index ?? 0} 步开始：${action || "处理中"}`;
    }
    if (topic === "computer_use.step.completed") {
        return `Desktop Live 第 ${index ?? 0} 步完成：${action || "已完成"}`;
    }
    if (topic === "computer_use.step.failed") {
        return `Desktop Live 第 ${index ?? 0} 步失败：${action || "执行失败"}`;
    }
    if (topic === "computer_use.step.heartbeat") {
        return `Desktop Live 正在执行：${action || "处理中"}`;
    }
    if (topic === "computer_use.step.waiting_for_window") {
        return `Desktop Live 正在等待 ${action || "目标窗口"} 出现`;
    }
    if (topic === "computer_use.action.settle_wait_started") {
        return `Desktop Live 正在等待界面稳定`;
    }
    if (topic === "computer_use.action.settle_wait_completed") {
        return `Desktop Live 已确认界面稳定`;
    }
    if (topic === "computer_use.plan.started") {
        return `Desktop Live 开始执行计划`;
    }
    return "";
}

export function normalizePhoneRealtimeEvent(raw: unknown): PhoneRealtimeEvent | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }

    const direct = raw as PhoneRealtimeEvent;
    if (typeof direct.type === "string") {
        return direct;
    }

    const envelope = raw as RuntimeEnvelope;
    const payload = envelope.payload && typeof envelope.payload === "object" ? (envelope.payload as JsonRecord) : null;
    if (!payload) {
        return null;
    }

    if (typeof (payload as PhoneRealtimeEvent).type === "string") {
        return withEnvelopeFields(payload as PhoneRealtimeEvent, envelope);
    }

    if (envelope.topic === "approval.requested") {
        const request = (payload.request as JsonRecord | undefined) || {};
        return withEnvelopeFields({
            type: "custom_event",
            name: "ask_user",
            data: {
                question: typeof request.question === "string"
                    ? request.question
                    : typeof request.prompt === "string"
                        ? request.prompt
                        : "我需要您的输入以继续执行任务。",
                toolCallId: typeof request.toolCallId === "string"
                    ? request.toolCallId
                    : typeof payload.approval_id === "string"
                        ? payload.approval_id
                        : "",
                approvalId: typeof payload.approval_id === "string" ? payload.approval_id : undefined,
                approvalKind: typeof payload.approval_kind === "string" ? payload.approval_kind : undefined,
                interactionKind: typeof request.interactionKind === "string" ? request.interactionKind : undefined,
                request,
            },
        }, envelope);
    }

    if (["run.paused", "run.cancelled", "run.interrupted"].includes(String(envelope.topic || ""))) {
        return withEnvelopeFields({
            type: "custom_event",
            name: "run_controlled",
            data: {
                topic: envelope.topic,
                ...payload,
            },
        }, envelope);
    }

    if (envelope.topic === "artifact.recorded") {
        return withEnvelopeFields({
            type: "custom_event",
            name: "artifact_recorded",
            data: {
                artifact: payload,
            },
            artifact: buildArtifact(payload) || undefined,
        }, envelope);
    }

    if (typeof envelope.topic === "string" && envelope.topic.startsWith("computer_use.")) {
        const label = buildProgressLabel(envelope.topic, payload);
        if (label) {
            return withEnvelopeFields({
                type: "custom_event",
                name: "runtime_progress",
                data: {
                    ...payload,
                    topic: envelope.topic,
                    label,
                },
            }, envelope);
        }
    }

    if (typeof envelope.topic === "string" && envelope.topic.trim()) {
        return withEnvelopeFields({
            type: "custom_event",
            name: "runtime_event",
            data: {
                ...payload,
                topic: envelope.topic,
            },
        }, envelope);
    }

    return null;
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
