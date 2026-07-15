"use client";

import dynamic from "next/dynamic";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { InputArea } from "@/components/chat/InputArea";
import { useLangGraphStream } from "@/hooks/use-langgraph-stream";
import {
    cloneMessages,
    normalizeMessagesForState,
    normalizeProjectedMessages,
    WEB_STREAM_LIFECYCLE_OPTIONS,
} from "@/lib/chat-stream-state";
import { normalizeRealtimeEvent } from "@/lib/realtime";
import { clearLegacyWebConversationCache } from "@/lib/web-conversation-cache";
import {
    buildRuntimeStageModel,
    buildRuntimeTimelineEntryFromEvent,
    mergeRuntimeTimeline,
    normalizeRuntimeTimeline,
} from "@/lib/runtime-stage";
import {
    markStreamClientCommit,
    markStreamClientRender,
    readStreamDiagnostics,
    recordReceivedStreamDelta,
    type PendingStreamDiagnostic,
    type StreamLatencyStats,
} from "@/lib/streaming-diagnostics";
import { Message } from "@/store/chat-types";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { CreateConversationPayload, useConversationContext } from "@/context/ConversationContext";
import { signIn, useSession } from "next-auth/react";
import {
    AlertCircle,
    ChevronDown,
    CornerDownRight,
    Edit3,
    GripVertical,
    Loader2,
    MoreHorizontal,
    PanelRight,
    PlugZap,
    TerminalSquare,
    Trash2,
} from "lucide-react";
import { resolveProfileAvatarSrc, useClientProfile } from "@/hooks/use-client-profile";
import { Button } from "@/components/ui/button";
import { WorkbenchShell } from "@/components/workbench/WorkbenchShell";
import { createSessionOverviewDocument } from "@/lib/workbench";
import { ingestWorkbenchRuntimeEvent, useWorkbenchStore } from "@/store/workbench-store";
import {
    ManualTerminalPanel,
    type ManualTerminalSessionView,
    type TerminalProfileView,
} from "@/components/chat/ManualTerminalPanel";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import {
    createInitialSessionRealtimeMessageState,
    type AdminProcessRef,
    deriveMemoryRuntimeInsightFromGovernance,
    deriveAuthoritativeSessionView,
    flushQueuedSessionRealtimeRuntimeEvents,
    normalizeContextGovernanceDigest,
    normalizeContextGovernanceHistory,
    contextUsagePercent as resolveContextUsagePercent,
    queueSessionRealtimeRuntimeEvent,
    syncSessionRealtimeMessageState,
    type AuthoritativeSessionView,
} from "@v8/session-realtime";

const AskUserModal = dynamic(
    () => import("@/components/chat/AskUserModal").then((mod) => mod.AskUserModal),
    { ssr: false }
);

const GovernanceApprovalModal = dynamic(
    () => import("@/components/chat/GovernanceApprovalModal").then((mod) => mod.GovernanceApprovalModal),
    { ssr: false }
);

const DEFAULT_LOCAL_ADMIN_BASE_URL = "http://127.0.0.1:9528";

interface ProjectDescriptor {
    id: string;
    name: string;
    description?: string;
    workspaceId?: string;
    workspacePath?: string;
    defaultScope?: string;
    tags?: string[];
    active?: boolean;
    workspaceTrustState?: "trusted" | "restricted";
    workspaceTrustSource?: string;
}

type WorkspaceBindingDraft =
    | { kind: "main" }
    | { kind: "project"; projectId: string }

function isWorkspaceTrustRequiredPayload(payload: Record<string, unknown>) {
    const detail = payload.detail;
    if (detail === "workspace_trust_required" || payload.error === "workspace_trust_required") {
        return true;
    }
    return Boolean(detail && typeof detail === "object" && (detail as Record<string, unknown>).error === "workspace_trust_required");
}

interface ScopeBindingView {
    projectId?: string;
    workspaceId?: string;
    workspacePath?: string;
    resolvedScope: string;
    scopeSource?: string;
    scopeConfidence?: number;
}

interface RunRecordView {
    id: string;
    status: string;
    started_at?: string;
    finished_at?: string;
    metadata?: Record<string, unknown>;
}

interface SupervisorReasoningEffortControl {
    visible?: boolean;
    supported?: boolean;
    levels?: string[];
    defaultLevel?: string;
    modelRef?: string;
}

type SessionProjectionView = AuthoritativeSessionView & {
    contextGovernance?: Record<string, unknown> | null;
    contextGovernanceHistory?: Record<string, unknown>[];
};

type QueuedChatMessage = {
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

type ChatQueueSubmitResponse = {
    accepted?: boolean;
    queued?: boolean;
    queuedMessage?: QueuedChatMessage | null;
    clientMessageId?: string;
    run_id?: string;
    runId?: string;
    error?: string;
};

type ContextSessionReference = {
    sessionId: string;
    source: "history_menu";
};

const CONTEXT_SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$/;

function isLegacyChatUnsupportedPayload(value: unknown) {
    const root = value && typeof value === "object" ? value as Record<string, unknown> : {};
    const snapshot = root.snapshot && typeof root.snapshot === "object" ? root.snapshot as Record<string, unknown> : {};
    return Boolean(root.legacyChatUnsupported || snapshot.legacyChatUnsupported);
}

function asPlainRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function readString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function isWorkspaceBindingErrorMessage(value: unknown) {
    const text = String(value || "").toLowerCase();
    return text.includes("workspace_binding_required")
        || text.includes("workspace_trust_required")
        || text.includes("workspace_side_effect_blocked");
}

function readErrorPayloadMessage(payload: Record<string, unknown>) {
    const detail = asPlainRecord(payload.detail);
    return readString(detail.error)
        || readString(detail.summary)
        || readString(detail.recommendedNextAction)
        || readString(payload.error)
        || readString(payload.message);
}

function normalizeQueuedMessage(value: unknown): QueuedChatMessage | null {
    const record = asPlainRecord(value);
    const id =
        readString(record.id)
        || readString(record.queueMessageId)
        || readString(record.guidanceQueueMessageId);
    if (!id) {
        return null;
    }

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

function extractQueuedMessages(value: unknown): QueuedChatMessage[] | null {
    const root = asPlainRecord(value);
    const snapshot = asPlainRecord(root.snapshot);
    const candidates = [root.queuedMessages, snapshot.queuedMessages];
    for (const candidate of candidates) {
        if (!Array.isArray(candidate)) {
            continue;
        }
        return candidate
            .map(normalizeQueuedMessage)
            .filter((item): item is QueuedChatMessage => Boolean(item));
    }
    return null;
}

function isVisibleQueuedMessage(item: QueuedChatMessage) {
    const state = String(item.state || "pending").trim().toLowerCase();
    return !["cancelled", "consumed", "injected"].includes(state);
}

function sortQueuedMessages(items: QueuedChatMessage[]) {
    return [...items].sort((left, right) => {
        const leftOrdinal = Number(left.ordinal);
        const rightOrdinal = Number(right.ordinal);
        if (Number.isFinite(leftOrdinal) && Number.isFinite(rightOrdinal) && leftOrdinal !== rightOrdinal) {
            return leftOrdinal - rightOrdinal;
        }
        return String(left.createdAt || left.updatedAt || left.id).localeCompare(String(right.createdAt || right.updatedAt || right.id));
    });
}

function normalizeScopeBinding(raw: unknown): ScopeBindingView | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }

    const record = raw as Record<string, unknown>;
    const resolvedScope = (record.resolved_scope || record.resolvedScope) as string | undefined;
    if (!resolvedScope) {
        return null;
    }

    return {
        projectId: (record.project_id || record.projectId) as string | undefined,
        workspaceId: (record.workspace_id || record.workspaceId) as string | undefined,
        workspacePath: (record.workspace_path || record.workspacePath) as string | undefined,
        resolvedScope,
        scopeSource: (record.scope_source || record.scopeSource) as string | undefined,
        scopeConfidence: Number(record.scope_confidence || record.scopeConfidence || 0) || undefined,
    };
}

function normalizeWorkflowStatusForRunBar(status?: string | null): string | undefined {
    if (!status) return undefined;
    if (status === "recoverable_failed") return "failed";
    return status;
}

function deriveHistoryPreview(
    messages: Message[],
    projectionSummary?: AuthoritativeSessionView["summary"] | null,
): string | undefined {
    const projectedPreview = String(
        projectionSummary?.previewExcerpt
        || (projectionSummary as Record<string, unknown> | null)?.lastNarrativeExcerpt
        || ""
    ).trim();
    if (projectedPreview) {
        return projectedPreview.slice(0, 120);
    }

    for (const message of [...messages].reverse()) {
        if (message.role !== "assistant" && message.role !== "user") {
            continue;
        }
        const content = String(message.content || "").trim();
        if (content) {
            return content.slice(0, 120);
        }
    }
    return undefined;
}

function findPendingAskUserToolCall(messages: Message[]) {
    const completedToolCallIds = new Set<string>();
    for (const message of messages) {
        for (const node of message.nodes || []) {
            if (node.kind !== "execution" || node.executionType !== "tool_result") {
                continue;
            }
            const toolName = readString(node.toolName);
            const toolCallId = readString(node.toolCallId);
            if (toolName === "ask_user" && toolCallId) {
                completedToolCallIds.add(toolCallId);
            }
        }
    }

    for (const message of [...messages].reverse()) {
        for (const node of [...(message.nodes || [])].reverse()) {
            if (node.kind !== "execution" || node.executionType !== "tool_call") {
                continue;
            }
            const toolName = readString(node.toolName);
            const toolCallId = readString(node.toolCallId);
            if (toolName !== "ask_user" || !toolCallId || completedToolCallIds.has(toolCallId)) {
                continue;
            }
            const args = asPlainRecord(node.args);
            const request = asPlainRecord(args.request);
            const question =
                readString(args.question)
                || readString(args.prompt)
                || readString(request.question)
                || readString(request.prompt);
            if (!question) {
                continue;
            }
            return {
                toolCallId,
                question,
                request: {
                    ...request,
                    ...args,
                    question,
                    prompt: readString(args.prompt) || readString(request.prompt) || question,
                    toolCallId,
                    interactionKind: "ask_user",
                },
            };
        }
    }
    return null;
}

type QueueUiLabels = {
    title: string;
    hint: string;
    pending: string;
    promoted: string;
    empty: string;
    guide: string;
    edit: string;
    closeQueue: string;
    collapse: string;
    expand: string;
    editTitle: string;
    editHint: string;
    editPlaceholder: string;
    cancel: string;
    save: string;
};

function QueuedMessagesStrip({
    messages,
    collapsed,
    menuOpenId,
    busyId,
    labels,
    onToggleCollapsed,
    onOpenMenu,
    onPromote,
    onCancel,
    onEdit,
}: {
    messages: QueuedChatMessage[];
    collapsed: boolean;
    menuOpenId: string | null;
    busyId: string;
    labels: QueueUiLabels;
    onToggleCollapsed: () => void;
    onOpenMenu: (id: string | null) => void;
    onPromote: (item: QueuedChatMessage) => void;
    onCancel: (item: QueuedChatMessage) => void;
    onEdit: (item: QueuedChatMessage) => void;
}) {
    if (messages.length === 0) {
        return null;
    }

    return (
        <section className="mx-auto w-full max-w-4xl overflow-hidden rounded-[1.15rem] border border-border/60 bg-background/82 shadow-[0_12px_32px_rgba(15,23,42,0.08)] backdrop-blur-xl dark:bg-zinc-900/72 dark:shadow-[0_18px_48px_rgba(0,0,0,0.26)]">
            <button
                type="button"
                className="flex h-9 w-full items-center gap-2 border-b border-border/45 px-3 text-left text-xs text-muted-foreground transition hover:bg-muted/35"
                onClick={onToggleCollapsed}
                aria-expanded={!collapsed}
            >
                <CornerDownRight className="h-3.5 w-3.5 shrink-0" />
                <span className="font-medium text-foreground">{labels.title}</span>
                <span className="rounded-full border border-border/60 bg-muted/45 px-1.5 py-0.5 text-[10px] leading-none text-muted-foreground">
                    {messages.length}
                </span>
                <span className="min-w-0 flex-1 truncate">{labels.hint}</span>
                <ChevronDown
                    className={cn(
                        "h-3.5 w-3.5 shrink-0 transition-transform",
                        collapsed && "-rotate-90",
                    )}
                    aria-label={collapsed ? labels.expand : labels.collapse}
                />
            </button>
            {!collapsed ? (
                <div className="max-h-36 overflow-y-auto px-2 py-1.5">
                    {messages.map((item, index) => {
                        const state = String(item.state || "pending").trim().toLowerCase();
                        const promoted = state === "promoted";
                        const itemBusy = busyId === item.id;
                        return (
                            <div
                                key={item.id}
                                className={cn(
                                    "group relative flex min-h-9 items-center gap-2 rounded-xl px-2 py-1.5 text-sm transition",
                                    "hover:bg-muted/40",
                                    promoted && "border border-primary/25 bg-primary/5",
                                )}
                            >
                                <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted-foreground/45" />
                                <CornerDownRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
                                <span className="w-5 shrink-0 text-xs font-semibold tabular-nums text-muted-foreground">
                                    {item.ordinal || index + 1}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <div className="truncate font-medium text-foreground">
                                        {item.content || labels.empty}
                                    </div>
                                    <div className={cn("text-[11px] leading-4", promoted ? "text-primary" : "text-muted-foreground")}>
                                        {promoted ? labels.promoted : labels.pending}
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    className={cn(
                                        "inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 text-xs font-medium text-muted-foreground transition hover:bg-muted hover:text-foreground",
                                        promoted && "pointer-events-none opacity-45",
                                    )}
                                    disabled={promoted || itemBusy}
                                    onClick={() => onPromote(item)}
                                >
                                    {itemBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CornerDownRight className="h-3.5 w-3.5" />}
                                    <span>{labels.guide}</span>
                                </button>
                                <button
                                    type="button"
                                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive"
                                    disabled={itemBusy}
                                    onClick={() => onCancel(item)}
                                    aria-label={labels.closeQueue}
                                    title={labels.closeQueue}
                                >
                                    {itemBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                                </button>
                                <div className="relative">
                                    <button
                                        type="button"
                                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
                                        onClick={() => onOpenMenu(menuOpenId === item.id ? null : item.id)}
                                        aria-label={labels.edit}
                                        title={labels.edit}
                                    >
                                        <MoreHorizontal className="h-3.5 w-3.5" />
                                    </button>
                                    {menuOpenId === item.id ? (
                                        <div className="absolute bottom-full right-0 z-[90] mb-1 w-36 overflow-hidden rounded-xl border border-border/65 bg-popover/98 p-1 text-popover-foreground shadow-[0_18px_48px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:shadow-[0_18px_48px_rgba(0,0,0,0.34)]">
                                            <button
                                                type="button"
                                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition hover:bg-muted"
                                                onClick={() => onEdit(item)}
                                                disabled={promoted}
                                            >
                                                <Edit3 className="h-3.5 w-3.5" />
                                                <span>{labels.edit}</span>
                                            </button>
                                            <button
                                                type="button"
                                                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-destructive transition hover:bg-destructive/10"
                                                onClick={() => onCancel(item)}
                                            >
                                                <Trash2 className="h-3.5 w-3.5" />
                                                <span>{labels.closeQueue}</span>
                                            </button>
                                        </div>
                                    ) : null}
                                </div>
                            </div>
                        );
                    })}
                </div>
            ) : null}
        </section>
    );
}

function QueuedMessageEditDialog({
    item,
    value,
    busy,
    labels,
    onChange,
    onCancel,
    onSave,
}: {
    item: QueuedChatMessage | null;
    value: string;
    busy: boolean;
    labels: QueueUiLabels;
    onChange: (value: string) => void;
    onCancel: () => void;
    onSave: () => void;
}) {
    if (!item) {
        return null;
    }

    return (
        <div className="fixed inset-0 z-[120] flex items-end justify-center bg-black/30 px-4 pb-6 backdrop-blur-sm sm:items-center sm:pb-0">
            <div className="w-full max-w-lg rounded-2xl border border-border/70 bg-background p-4 shadow-[0_24px_80px_rgba(15,23,42,0.24)] dark:shadow-[0_24px_80px_rgba(0,0,0,0.42)]">
                <div className="mb-3">
                    <h2 className="text-base font-semibold text-foreground">{labels.editTitle}</h2>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">{labels.editHint}</p>
                </div>
                <textarea
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    placeholder={labels.editPlaceholder}
                    className="min-h-28 w-full resize-none rounded-xl border border-border/70 bg-muted/25 px-3 py-2 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/55 focus:border-primary/45 focus:ring-2 focus:ring-primary/15"
                />
                <div className="mt-4 flex justify-end gap-2">
                    <button
                        type="button"
                        className="rounded-xl border border-border/70 bg-background px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
                        onClick={onCancel}
                    >
                        {labels.cancel}
                    </button>
                    <button
                        type="button"
                        className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-55"
                        disabled={busy || !value.trim()}
                        onClick={onSave}
                    >
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        {labels.save}
                    </button>
                </div>
            </div>
        </div>
    );
}

function buildWebMessageComparisonKeys(message: Message) {
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

function hasStructuredAssistantPayload(message: Message | null | undefined) {
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

function hasRenderableWebMessagePayload(message: Message | null | undefined) {
    return Boolean(
        message
        && (
            String(message.content || "").trim()
            || (Array.isArray(message.nodes) && message.nodes.length > 0)
            || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
            || (Array.isArray(message.images) && message.images.length > 0)
        ),
    );
}

function attachSseEventId(payload: unknown, event: MessageEvent) {
    const eventId = String(event.lastEventId || "").trim();
    if (!eventId || !payload || typeof payload !== "object" || Array.isArray(payload)) {
        return payload;
    }
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

function mergeWebMessagePayload(base: Message, incoming: Message): Message {
    const incomingTranscriptVersion = Number((incoming.metadata || {}).transcriptVersion || 0);
    const incomingCanonical = incomingTranscriptVersion > 0 || (incoming.nodes?.length || 0) > 0;
    const merged: Message = {
        ...base,
        ...incoming,
        metadata: {
            ...(base.metadata || {}),
            ...(incoming.metadata || {}),
        },
    };
    if (!incomingCanonical && (base.nodes?.length || 0) > (incoming.nodes?.length || 0)) {
        merged.nodes = base.nodes;
    }
    if (!incomingCanonical && (base.artifacts?.length || 0) > (incoming.artifacts?.length || 0)) {
        merged.artifacts = base.artifacts;
    }
    if (!incomingCanonical && (base.images?.length || 0) > (incoming.images?.length || 0)) {
        merged.images = base.images;
    }
    if (!merged.agentName && base.agentName) merged.agentName = base.agentName;
    if (!merged.agentAvatar && base.agentAvatar) merged.agentAvatar = base.agentAvatar;
    if (!merged.agentRoleLabel && base.agentRoleLabel) merged.agentRoleLabel = base.agentRoleLabel;
    if (!merged.toolInvocations?.length && base.toolInvocations?.length) {
        merged.toolInvocations = base.toolInvocations;
    }
    return merged;
}

function mergeProjectedSnapshotMessages(current: Message[], projectedMessages: unknown[]) {
    const normalizedSnapshot = normalizeProjectedMessages(projectedMessages);
    if (current.length === 0) {
        return normalizeMessagesForState(normalizedSnapshot);
    }
    const currentByKey = new Map<string, Message>();
    current.forEach((message) => {
        buildWebMessageComparisonKeys(message).forEach((key) => {
            if (!currentByKey.has(key)) {
                currentByKey.set(key, message);
            }
        });
    });
    return normalizeMessagesForState(
        normalizedSnapshot.map((snapshotMessage) => {
            const matchingCurrent = buildWebMessageComparisonKeys(snapshotMessage)
                .map((key) => currentByKey.get(key))
                .find(Boolean);
        if (!matchingCurrent) {
            return snapshotMessage;
        }
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) {
            return snapshotMessage;
        }
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant"
            && hasRenderableWebMessagePayload(snapshotMessage)
            && hasStructuredAssistantPayload(snapshotMessage);
        if (!snapshotAuthoritativeAssistant) {
            return mergeWebMessagePayload(matchingCurrent, snapshotMessage);
        }
        return {
            ...snapshotMessage,
            metadata: {
                ...(matchingCurrent.metadata || {}),
                ...(snapshotMessage.metadata || {}),
            },
            images: (snapshotMessage.images?.length || 0) > 0 ? snapshotMessage.images : matchingCurrent.images,
            artifacts: (snapshotMessage.artifacts?.length || 0) > 0 ? snapshotMessage.artifacts : matchingCurrent.artifacts,
            toolInvocations: (snapshotMessage.toolInvocations?.length || 0) > 0 ? snapshotMessage.toolInvocations : matchingCurrent.toolInvocations,
        };
    }),
  );
}

function dedupeProcesses(processes: AdminProcessRef[]) {
    return Array.from(
        new Map(
            processes
                .filter((process) => String(process.processId || process.commandId || "").trim())
                .map((process) => [String(process.processId || process.commandId || "").trim(), process]),
        ).values(),
    );
}

function isActiveTerminalProcess(process: AdminProcessRef) {
    const status = String(process.status || '').trim().toLowerCase();
    return Boolean(process.canInput) && !['stopped', 'terminated', 'completed', 'failed'].includes(status);
}

function terminalTabIdForManualSession(sessionId: string) {
    const normalized = String(sessionId || '').trim();
    return normalized ? `manual:${normalized}` : '';
}

function terminalTabIdForProcess(process: AdminProcessRef) {
    const normalized = String(process.processId || process.commandId || '').trim();
    return normalized ? `process:${normalized}` : '';
}

function terminalHiddenStorageKey(conversationId: string) {
    return `v8-web-terminal-hidden-tabs:${conversationId}`;
}

function filterConversationProcesses(
    processes: AdminProcessRef[],
    {
        activeConversationId,
        currentConversationRunId,
        messageIds,
    }: {
        activeConversationId: string | null;
        currentConversationRunId: string;
        messageIds: Set<string>;
    },
) {
    return processes.filter((process) => {
        if (!activeConversationId) {
            return true;
        }
        const processSessionId = String((process as AdminProcessRef & { sessionId?: string | null }).sessionId || "").trim();
        if (processSessionId) {
            return processSessionId === activeConversationId;
        }
        const processRunId = String(process.runId || "").trim();
        if (currentConversationRunId && processRunId) {
            return processRunId === currentConversationRunId;
        }
        const sourceMessageId = String(process.sourceMessageId || "").trim();
        if (sourceMessageId) {
            return messageIds.has(sourceMessageId);
        }
        return true;
    });
}

function approvalRequestRecord(approval: Record<string, unknown> | null | undefined) {
    const request = approval?.request;
    return request && typeof request === "object" && !Array.isArray(request)
        ? request as Record<string, unknown>
        : {};
}

function readApprovalString(record: Record<string, unknown>, ...keys: string[]) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return "";
}

function isSpecStageApproval(approval: Record<string, unknown> | null | undefined) {
    const request = approvalRequestRecord(approval);
    const kind = String(
        approval?.approval_kind
        || request.approvalKind
        || request.approval_kind
        || "",
    ).trim().toLowerCase();
    return kind === "spec_stage_approval";
}

function buildSpecReviewHref(approval: Record<string, unknown>, fallbackWorkspacePath = "") {
    const request = approvalRequestRecord(approval);
    const params = new URLSearchParams();
    const workspacePath = readApprovalString(request, "workspacePath", "workspace_path") || fallbackWorkspacePath;
    const specId = readApprovalString(request, "specId", "spec_id");
    const stage = readApprovalString(request, "stage", "specStage", "spec_stage");
    if (workspacePath) params.set("workspace", workspacePath);
    if (specId) params.set("specId", specId);
    if (stage) params.set("stage", stage);
    return `/specs${params.toString() ? `?${params.toString()}` : ""}`;
}



export default function ChatClient() {
    const t = useT();
    const { locale } = useLocale();
    const { status, data: session } = useSession();
    const { profile: clientProfile } = useClientProfile();
    const searchParams = useSearchParams();
    const urlId = searchParams.get("id");
    const newConversationIntent = searchParams.get("new") === "1";
    const contextSessionIdParam = String(searchParams.get("contextSessionId") || "").trim();
    const router = useRouter();
    const [localConnectError, setLocalConnectError] = useState<string | null>(null);
    const localConnectAttemptedRef = useRef(false);
    const queueLabels = useMemo<QueueUiLabels>(() => ({
        title: t("web.generated.8d4c2b7a1f"),
        hint: t("web.generated.7a91e0c3b6"),
        pending: t("web.generated.0f6b2c9a73"),
        promoted: t("web.generated.4e8f12b6a0"),
        empty: t("web.generated.1b7fd0e9aa"),
        guide: t("web.generated.a34c51d8f2"),
        edit: t("web.generated.c72a903e1b"),
        closeQueue: t("web.generated.f94b0a2d67"),
        collapse: t("web.generated.918a0d3c57"),
        expand: t("web.generated.2e76f8a904"),
        editTitle: t("web.generated.93d7a85c10"),
        editHint: t("web.generated.e1bf640a52"),
        editPlaceholder: t("web.generated.d06b4c35e9"),
        cancel: t("web.generated.b8a761d42c"),
        save: t("web.generated.52ae091fdd"),
    }), [t]);

    // Use a true React state to track the active conversation ID.
    // This is crucial because window.history.replaceState does not trigger Next.js router updates,
    // which would cause `sendMessage` to send `conversationId: null` on subsequent messages 
    // and spawn duplicate history entries.
    const [activeConversationId, setActiveConversationId] = useState<string | null>(urlId);
    const [pendingContextSessionRefs, setPendingContextSessionRefs] = useState<ContextSessionReference[]>(() => (
        newConversationIntent && CONTEXT_SESSION_ID_PATTERN.test(contextSessionIdParam)
            ? [{ sessionId: contextSessionIdParam, source: "history_menu" }]
            : []
    ));
    const contextTakeoverConversationIdRef = useRef<string | null>(null);
    const clearPendingContextSessionRefs = useCallback(() => {
        contextTakeoverConversationIdRef.current = null;
        setPendingContextSessionRefs([]);
    }, []);

    useEffect(() => {
        setActiveConversationId(urlId);
    }, [urlId]);

    useEffect(() => {
        window.v8osShell?.reportActiveSession(activeConversationId);
    }, [activeConversationId]);

    useEffect(() => {
        if (newConversationIntent && CONTEXT_SESSION_ID_PATTERN.test(contextSessionIdParam)) {
            contextTakeoverConversationIdRef.current = null;
            setPendingContextSessionRefs([{ sessionId: contextSessionIdParam, source: "history_menu" }]);
            return;
        }
        if (!newConversationIntent && urlId !== contextTakeoverConversationIdRef.current) {
            clearPendingContextSessionRefs();
        }
    }, [clearPendingContextSessionRefs, contextSessionIdParam, newConversationIntent, urlId]);

    // Track which conversation is currently streaming to prevent overwriting state
    const streamingConversationIdRef = useRef<string | null>(null);

    // Sound Effect Logic
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const lastMessageIdRef = useRef<string | null>(null);
    const lastMessageLengthRef = useRef<number>(0);

    // Initialize Audio
    useEffect(() => {
        audioRef.current = new Audio("/message-pop.mp3");
        audioRef.current.volume = 0.5;
    }, []);

    useEffect(() => {
        void clearLegacyWebConversationCache();
    }, []);

    const [input, setInput] = useState("");
    const { conversations, refreshConversations, createConversation, patchConversationSummary } = useConversationContext();
    const [askUserModalOpen, setAskUserModalOpen] = useState(false);
    const [askUserQuestion, setAskUserQuestion] = useState("");
    const [askUserToolCallId, setAskUserToolCallId] = useState("");
    const [askUserApprovalId, setAskUserApprovalId] = useState("");
    const [askUserRequest, setAskUserRequest] = useState<Record<string, unknown> | null>(null);
    const [askUserCollapsed, setAskUserCollapsed] = useState(false);
    const [governanceApprovalOpen, setGovernanceApprovalOpen] = useState(false);
    const [governanceApprovalBusy, setGovernanceApprovalBusy] = useState(false);
    const [dismissedGovernanceApprovalId, setDismissedGovernanceApprovalId] = useState("");
    const [projects, setProjects] = useState<ProjectDescriptor[]>([]);
    const [mainWorkspacePath, setMainWorkspacePath] = useState("");
    const [workspaceChooserVisible, setWorkspaceChooserVisible] = useState(false);
    const [workspaceChooserBusy, setWorkspaceChooserBusy] = useState(false);
    const [newProjectPath, setNewProjectPath] = useState("");
    const [scopeBinding, setScopeBinding] = useState<ScopeBindingView | null>(null);
    const [, setScopeLoading] = useState(false);
    const [projectsLoading, setProjectsLoading] = useState(false);
    const [runEntries, setRunEntries] = useState<RunRecordView[]>([]);
    const [supervisorReasoningEffortControl, setSupervisorReasoningEffortControl] = useState<SupervisorReasoningEffortControl | null>(null);
    const [sessionProjection, setSessionProjection] = useState<SessionProjectionView | null>(null);
    const [legacyChatUnsupported, setLegacyChatUnsupported] = useState(false);
    const [hasOlderTurns, setHasOlderTurns] = useState(false);
    const [isLoadingOlderTurns, setIsLoadingOlderTurns] = useState(false);
    const [queuedMessages, setQueuedMessages] = useState<QueuedChatMessage[]>([]);
    const [queuedMessagesCollapsed, setQueuedMessagesCollapsed] = useState(false);
    const [queuedMessageMenuId, setQueuedMessageMenuId] = useState<string | null>(null);
    const [queuedMessageBusyId, setQueuedMessageBusyId] = useState("");
    const [editingQueuedMessage, setEditingQueuedMessage] = useState<QueuedChatMessage | null>(null);
    const [queuedMessageEditText, setQueuedMessageEditText] = useState("");
    const [queuedMessageEditBusy, setQueuedMessageEditBusy] = useState(false);
    const [queuedMessageError, setQueuedMessageError] = useState("");
    const [sessionProcessSurface, setSessionProcessSurface] = useState<AdminProcessRef[]>([]);
    const lastSessionProcessSurfaceAtRef = useRef(0);
    const workbenchMode = useWorkbenchStore((state) => state.mode);
    const bindWorkbenchSession = useWorkbenchStore((state) => state.bindSession);
    const toggleWorkbench = useWorkbenchStore((state) => state.toggle);
    const [terminalOpen, setTerminalOpen] = useState(() => {
        if (typeof window !== "undefined") {
            const val = localStorage.getItem("v8-web-terminal-open");
            return val === "true";
        }
        return false;
    });
    const [terminalProfiles, setTerminalProfiles] = useState<TerminalProfileView[]>([]);
    const [terminalProfileId, setTerminalProfileId] = useState("");
    const [manualTerminalSessions, setManualTerminalSessions] = useState<ManualTerminalSessionView[]>([]);
    const [activeTerminalTabId, setActiveTerminalTabId] = useState("");
    const [hiddenTerminalTabIds, setHiddenTerminalTabIds] = useState<Set<string>>(() => new Set());
    const autoActivatedProcessTerminalIdsRef = useRef<Set<string>>(new Set());
    const [terminalBusy, setTerminalBusy] = useState(false);
    const [terminalError, setTerminalError] = useState("");

    useEffect(() => {
        bindWorkbenchSession(activeConversationId || null);
    }, [activeConversationId, bindWorkbenchSession]);

    useEffect(() => {
        if (typeof window !== "undefined") {
            localStorage.setItem("v8-web-terminal-open", String(terminalOpen));
        }
    }, [terminalOpen]);

    useEffect(() => {
        autoActivatedProcessTerminalIdsRef.current = new Set();
        setManualTerminalSessions([]);
        setActiveTerminalTabId("");
        if (typeof window === "undefined" || !activeConversationId) {
            setHiddenTerminalTabIds(new Set());
            return;
        }
        try {
            const raw = localStorage.getItem(terminalHiddenStorageKey(activeConversationId));
            const parsed = raw ? JSON.parse(raw) : [];
            setHiddenTerminalTabIds(new Set(Array.isArray(parsed) ? parsed.map((item) => String(item || "")).filter(Boolean) : []));
        } catch {
            setHiddenTerminalTabIds(new Set());
        }
    }, [activeConversationId]);

    useEffect(() => {
        if (typeof window === "undefined" || !activeConversationId) {
            return;
        }
        localStorage.setItem(terminalHiddenStorageKey(activeConversationId), JSON.stringify(Array.from(hiddenTerminalTabIds)));
    }, [activeConversationId, hiddenTerminalTabIds]);

    const [localHour, setLocalHour] = useState<number>(9);
    const viewportBaselineRef = useRef(0);
    const [mobileKeyboardInset, setMobileKeyboardInset] = useState(0);
    const chatUserName = useMemo(
        () => clientProfile?.name
            || session?.user?.name
            || session?.user?.login
            || session?.user?.email
            || "",
        [clientProfile?.name, session?.user?.email, session?.user?.login, session?.user?.name],
    );
    const chatUserAvatar = useMemo(
        () => resolveProfileAvatarSrc(clientProfile?.image || session?.user?.image || ""),
        [clientProfile?.image, session?.user?.image],
    );
    const terminalWorkspacePath = scopeBinding?.workspacePath || mainWorkspacePath || "";
    const hasActiveWorkbenchSession = Boolean(activeConversationId);

    const upsertQueuedMessage = useCallback((incoming: unknown) => {
        const normalized = normalizeQueuedMessage(incoming);
        if (!normalized) {
            return;
        }
        setQueuedMessages((current) => {
            const next = current.filter((item) => item.id !== normalized.id);
            return sortQueuedMessages([...next, normalized]);
        });
    }, []);

    const applyQueuedMessagesSnapshot = useCallback((incoming: QueuedChatMessage[] | null) => {
        if (!incoming) {
            return;
        }
        setQueuedMessages(sortQueuedMessages(incoming));
    }, []);

    const visibleQueuedMessages = useMemo(
        () => sortQueuedMessages(queuedMessages.filter(isVisibleQueuedMessage)),
        [queuedMessages],
    );

    const upsertManualTerminalSession = useCallback((payload: ManualTerminalSessionView, makeActive = false) => {
        const sessionId = String(payload?.sessionId || "").trim();
        if (!sessionId) {
            return;
        }
        const tabId = terminalTabIdForManualSession(sessionId);
        setManualTerminalSessions((prev) => {
            const index = prev.findIndex((item) => item.sessionId === sessionId);
            if (index < 0) {
                return [...prev, payload];
            }
            const next = [...prev];
            next[index] = { ...next[index], ...payload };
            return next;
        });
        if (tabId) {
            setHiddenTerminalTabIds((prev) => {
                if (!prev.has(tabId)) {
                    return prev;
                }
                const next = new Set(prev);
                next.delete(tabId);
                return next;
            });
        }
        if (makeActive) {
            setActiveTerminalTabId(tabId);
        }
    }, []);

    useEffect(() => {
        if (!terminalOpen) {
            return;
        }
        let cancelled = false;
        async function loadTerminalProfiles() {
            try {
                const response = await fetch("/api/client/terminal/profiles", { cache: "no-store" });
                const payload = await response.json().catch(() => ({}));
                if (cancelled) {
                    return;
                }
                if (!response.ok || payload?.ok === false) {
                    setTerminalError(String(payload?.error || payload?.detail || "终端配置不可用"));
                    return;
                }
                const profiles = Array.isArray(payload?.profiles) ? payload.profiles : [];
                setTerminalProfiles(profiles);
                setTerminalProfileId((prev) => prev || profiles[0]?.id || "");
            } catch (error) {
                if (!cancelled) {
                    setTerminalError(error instanceof Error ? error.message : "终端配置读取失败");
                }
            }
        }
        void loadTerminalProfiles();
        return () => {
            cancelled = true;
        };
    }, [terminalOpen]);

    const startManualTerminal = useCallback(async () => {
        if (terminalBusy) {
            return;
        }
        setTerminalBusy(true);
        setTerminalError("");
        try {
            const response = await fetch("/api/client/terminal/sessions", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    profileId: terminalProfileId || undefined,
                    cwd: terminalWorkspacePath || undefined,
                    conversationId: activeConversationId || undefined,
                    workspaceId: scopeBinding?.workspaceId || undefined,
                    projectId: scopeBinding?.projectId || undefined,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload?.ok === false) {
                setTerminalError(String(payload?.error || payload?.detail || "终端启动失败"));
                return;
            }
            upsertManualTerminalSession(payload, true);
            setTerminalProfileId((prev) => prev || payload?.profileId || "");
        } catch (error) {
            setTerminalError(error instanceof Error ? error.message : "终端启动失败");
        } finally {
            setTerminalBusy(false);
        }
    }, [
        activeConversationId,
        scopeBinding?.projectId,
        scopeBinding?.workspaceId,
        terminalBusy,
        terminalProfileId,
        terminalWorkspacePath,
        upsertManualTerminalSession,
    ]);

    const loadManualTerminalSessions = useCallback(async () => {
        if (!activeConversationId) {
            setManualTerminalSessions([]);
            return;
        }
        try {
            const response = await fetch(`/api/client/terminal/sessions?conversationId=${encodeURIComponent(activeConversationId)}`, {
                cache: "no-store",
            });
            if (!response.ok) {
                return;
            }
            const payload = await response.json().catch(() => ({}));
            const sessions = Array.isArray(payload?.sessions) ? payload.sessions as ManualTerminalSessionView[] : [];
            setManualTerminalSessions(sessions);
            setActiveTerminalTabId((current) => current || terminalTabIdForManualSession(sessions[0]?.sessionId || ""));
        } catch (error) {
            console.warn("[ChatClient] Failed to restore manual terminal sessions:", error);
        }
    }, [activeConversationId]);

    useEffect(() => {
        if (!terminalOpen || !activeConversationId) {
            return;
        }
        void loadManualTerminalSessions();
    }, [activeConversationId, loadManualTerminalSessions, terminalOpen]);

    const hideTerminalTab = useCallback((tabId: string) => {
        const normalized = String(tabId || "").trim();
        if (!normalized) {
            return;
        }
        setHiddenTerminalTabIds((prev) => {
            const next = new Set(prev);
            next.add(normalized);
            return next;
        });
        setActiveTerminalTabId((current) => current === normalized ? "" : current);
    }, []);

    const showHiddenTerminalTabs = useCallback(() => {
        setHiddenTerminalTabIds(new Set());
    }, []);

    useEffect(() => {
        if (typeof window === "undefined") {
            return;
        }

        const mediaQuery = window.matchMedia("(max-width: 767px)");
        const visualViewport = window.visualViewport;

        const updateMobileViewport = () => {
            if (!mediaQuery.matches) {
                viewportBaselineRef.current = 0;
                setMobileKeyboardInset(0);
                return;
            }

            const currentHeight = Math.round(visualViewport?.height ?? window.innerHeight);
            if (viewportBaselineRef.current === 0 || currentHeight > viewportBaselineRef.current) {
                viewportBaselineRef.current = currentHeight;
            }

            const baselineHeight = viewportBaselineRef.current || currentHeight;
            const offsetTop = Math.max(0, Math.round(visualViewport?.offsetTop ?? 0));
            const visualViewportInset = Math.max(0, Math.round(window.innerHeight - currentHeight - offsetTop));
            const shrinkInset = Math.max(0, baselineHeight - currentHeight);
            const nextInset = Math.max(visualViewportInset, shrinkInset > 96 ? shrinkInset : 0);

            setMobileKeyboardInset(nextInset > 24 ? nextInset : 0);
        };

        const handleViewportReset = () => {
            viewportBaselineRef.current = 0;
            updateMobileViewport();
        };

        updateMobileViewport();
        visualViewport?.addEventListener("resize", updateMobileViewport);
        visualViewport?.addEventListener("scroll", updateMobileViewport);
        window.addEventListener("resize", updateMobileViewport);
        window.addEventListener("orientationchange", handleViewportReset);

        if (typeof mediaQuery.addEventListener === "function") {
            mediaQuery.addEventListener("change", handleViewportReset);
        } else {
            mediaQuery.addListener(handleViewportReset);
        }

        return () => {
            visualViewport?.removeEventListener("resize", updateMobileViewport);
            visualViewport?.removeEventListener("scroll", updateMobileViewport);
            window.removeEventListener("resize", updateMobileViewport);
            window.removeEventListener("orientationchange", handleViewportReset);
            if (typeof mediaQuery.removeEventListener === "function") {
                mediaQuery.removeEventListener("change", handleViewportReset);
            } else {
                mediaQuery.removeListener(handleViewportReset);
            }
        };
    }, []);

    const loadRuns = useCallback(async (conversationId: string) => {
        try {
            const res = await fetch(`/api/runs?session_id=${encodeURIComponent(conversationId)}&limit=8`, { cache: "no-store" });
            if (!res.ok) {
                setRunEntries([]);
                return;
            }
            const data = await res.json().catch(() => ({}));
            setRunEntries(Array.isArray(data?.runs) ? data.runs : []);
        } catch (error) {
            console.warn("[ChatClient] Failed to load runs:", error);
            setRunEntries([]);
        }
    }, []);

    // Initialize Hook
    const { messages, isLoading, sendMessage, stop, setMessages, sendToolOutput, resolveApproval } = useLangGraphStream({
        apiEndpoint: `/api/chat`,
        onFinish: () => {
            refreshConversations();
            streamingConversationIdRef.current = null; // Reset when done
            const conversationId = activeConversationIdRef.current;
            if (conversationId) {
                void loadRuns(conversationId);
            }
        },
        onConnect: (newId) => {
            // Record that we are streaming this ID
            streamingConversationIdRef.current = newId;

            // Silently update URL if it changes (e.g. from new chat)
            if (activeConversationId !== newId) {
                console.log(`[ChatClient] Conversation ID established: ${newId}`);
                setActiveConversationId(newId); // Update React State immediately
                window.history.replaceState(null, '', `/chat?id=${newId}`);
                // NOTE: We don't use router.push/replace here to avoid triggering specific useEffect re-runs
                // that might reset the chat state.
            }
            void loadRuns(newId);
        },
        onError: (error) => {
            console.error("Chat error:", error);
            streamingConversationIdRef.current = null;
            if (error.message.includes("Conversation not found") || error.message.includes("404")) {
                router.replace('/chat');
            }
        },
        onCustomEvent: (event) => {
            if (event.name === "ask_user") {
                const eventData = asPlainRecord(event.data);
                const request = asPlainRecord(eventData.request);
                const interactionId = readString(eventData.interactionId) || readString(eventData.id) || readString(eventData.approvalId);
                applyAskUserPendingApproval({
                    id: interactionId,
                    interactionId,
                    approvalId: readString(eventData.approvalId),
                    run_id: readString(event.run_id) || readString(eventData.runId),
                    interactionKind: "ask_user",
                    question: readString(eventData.question),
                    toolCallId: readString(eventData.toolCallId),
                    request: {
                        ...request,
                        question: readString(request.question) || readString(eventData.question),
                        prompt: readString(request.prompt) || readString(eventData.prompt) || readString(eventData.question),
                        toolCallId: readString(request.toolCallId) || readString(eventData.toolCallId),
                        interactionKind: "ask_user",
                    },
                });
                const conversationId = activeConversationIdRef.current;
                if (conversationId) {
                    void loadRuns(conversationId);
                }
            }
            if (event.name === "human_guidance" || String(event.topic || "").startsWith("human_guidance.")) {
                const eventData = asPlainRecord(event.data);
                const queueMessage = normalizeQueuedMessage(eventData.queueMessage);
                if (queueMessage) {
                    upsertQueuedMessage(queueMessage);
                }
            }
            if (event.name === "run_controlled") {
                const conversationId = activeConversationIdRef.current;
                if (conversationId) {
                    void loadRuns(conversationId);
                }
            }
        }
    });

    const activeConversationIdRef = useRef<string | null>(activeConversationId);
    const isLoadingRef = useRef(isLoading);
    const messagesRef = useRef<Message[]>(messages);
    const realtimeMessageStateRef = useRef(
        createInitialSessionRealtimeMessageState<Message>([], WEB_STREAM_LIFECYCLE_OPTIONS),
    );
    const latestRealtimeSeqRef = useRef<number>(0);
    const runtimeFlushFrameRef = useRef<number | null>(null);
    const runtimeFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const turnBeforeCursorRef = useRef<string | null>(null);
    const isLoadingOlderTurnsRef = useRef(false);
    const historyPagingModeRef = useRef(false);
    const streamLatencyStatsRef = useRef(new Map<string, StreamLatencyStats>());
    const pendingStreamDiagnosticRef = useRef<PendingStreamDiagnostic | null>(null);
    const currentRun = sessionProjection?.currentRun || runEntries[0] || null;
    const activeConversationRunning = useMemo(() => {
        if (isLoading) return true;
        const activeConversation = conversations.find((item) => (item.sessionId || item.id) === activeConversationId);
        const activeStatuses = ["running", "queued", "pending", "starting", "streaming", "waiting_input", "waiting_approval", "waiting_external_tool", "paused"];
        const terminalStatuses = ["idle", "completed", "failed", "cancelled", "recoverable_failed", "degraded", "interrupted"];
        const observedStatuses = [
            activeConversation?.status,
            sessionProjection?.runtimeStatus,
            currentRun?.status,
        ].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
        if (observedStatuses.some((status) => activeStatuses.includes(status))) {
            return true;
        }
        if (observedStatuses.length > 0 && observedStatuses.every((status) => terminalStatuses.includes(status))) {
            return false;
        }
        return isLoading;
    }, [activeConversationId, conversations, currentRun?.status, isLoading, sessionProjection?.runtimeStatus]);
    const askUserPendingProjection = useMemo(
        () => (sessionProjection?.askUserInteractions || []).find((item) => String(item.status || "pending").toLowerCase() === "pending") || null,
        [sessionProjection?.askUserInteractions],
    );
    const governanceApprovals = useMemo(
        () => (sessionProjection?.approvals || []),
        [sessionProjection?.approvals],
    );
    const governancePendingApproval = governanceApprovals[0] || null;
    const governancePendingApprovalId = String(governancePendingApproval?.id || "").trim();
    const hasAskUserPending = Boolean(askUserApprovalId || askUserToolCallId);
    const projectionRunId = (sessionProjection?.controls?.runId || sessionProjection?.currentRun?.id || sessionProjection?.workflow?.rootRunId) ?? undefined;
    const effectiveStatus = hasAskUserPending
        ? "waiting_input"
        : governancePendingApprovalId || currentRun?.status === "waiting_approval"
            ? "waiting_approval"
        : sessionProjection?.runtimeStatus
            || currentRun?.status
            || normalizeWorkflowStatusForRunBar(sessionProjection?.controls?.workflowStatus)
            || normalizeWorkflowStatusForRunBar(sessionProjection?.workflow?.status);
    const effectivePendingApproval = Boolean(
        governancePendingApprovalId
        || currentRun?.status === "waiting_approval",
    );
    const projectionTodos = sessionProjection?.todos?.items || [];
    const projectionTodoStale = Boolean(sessionProjection?.todos?.isStale);
    const projectionProcesses = useMemo(
        () => {
            const messageIds = new Set(
                messages
                    .map((message) => String(message.id || "").trim())
                    .filter(Boolean),
            );
            const currentConversationRunId = String(currentRun?.id || projectionRunId || "").trim();
            const projectionScopedProcesses = filterConversationProcesses(
                dedupeProcesses(sessionProjection?.processes || []),
                {
                    activeConversationId,
                    currentConversationRunId,
                    messageIds,
                },
            );
            const sessionScopedProcesses = dedupeProcesses(
                (sessionProcessSurface || []).filter((process) => {
                    const processSessionId = String((process as AdminProcessRef & { sessionId?: string | null }).sessionId || "").trim();
                    return !activeConversationId || !processSessionId || processSessionId === activeConversationId;
                }),
            );
            return dedupeProcesses([
                ...projectionScopedProcesses,
                ...sessionScopedProcesses,
            ]);
        },
        [activeConversationId, currentRun?.id, messages, projectionRunId, sessionProcessSurface, sessionProjection?.processes],
    );
    const hudProcesses = useMemo(
        () => dedupeProcesses([
            ...projectionProcesses,
            ...dedupeProcesses(sessionProcessSurface || []),
        ]),
        [projectionProcesses, sessionProcessSurface],
    );
    const terminalProcesses = useMemo(
        () => hudProcesses.filter(isActiveTerminalProcess),
        [hudProcesses],
    );
    const visibleManualTerminalSessions = useMemo(
        () => manualTerminalSessions.filter((session) => !hiddenTerminalTabIds.has(terminalTabIdForManualSession(session.sessionId || ""))),
        [hiddenTerminalTabIds, manualTerminalSessions],
    );
    const visibleTerminalProcesses = useMemo(
        () => terminalProcesses.filter((process) => !hiddenTerminalTabIds.has(terminalTabIdForProcess(process))),
        [hiddenTerminalTabIds, terminalProcesses],
    );
    const hiddenTerminalTabCount = useMemo(() => {
        const allTabIds = [
            ...manualTerminalSessions.map((session) => terminalTabIdForManualSession(session.sessionId || "")),
            ...terminalProcesses.map((process) => terminalTabIdForProcess(process)),
        ].filter(Boolean);
        return allTabIds.filter((tabId) => hiddenTerminalTabIds.has(tabId)).length;
    }, [hiddenTerminalTabIds, manualTerminalSessions, terminalProcesses]);
    const visibleTerminalTabIds = useMemo(
        () => [
            ...visibleManualTerminalSessions.map((session) => terminalTabIdForManualSession(session.sessionId || "")),
            ...visibleTerminalProcesses.map((process) => terminalTabIdForProcess(process)),
        ].filter(Boolean),
        [visibleManualTerminalSessions, visibleTerminalProcesses],
    );
    const visibleTerminalTabKey = visibleTerminalTabIds.join("|");

    useEffect(() => {
        if (activeTerminalTabId && visibleTerminalTabIds.includes(activeTerminalTabId)) {
            return;
        }
        setActiveTerminalTabId(visibleTerminalTabIds[0] || "");
    }, [activeTerminalTabId, visibleTerminalTabKey, visibleTerminalTabIds]);

    useEffect(() => {
        const nextProcess = terminalProcesses.find((process) => {
            const processId = String(process.processId || process.commandId || "").trim();
            const tabId = terminalTabIdForProcess(process);
            return processId && tabId && !hiddenTerminalTabIds.has(tabId) && !autoActivatedProcessTerminalIdsRef.current.has(processId);
        });
        if (!nextProcess) {
            return;
        }
        const processId = String(nextProcess.processId || nextProcess.commandId || "").trim();
        autoActivatedProcessTerminalIdsRef.current.add(processId);
        setTerminalOpen(true);
        setActiveTerminalTabId(terminalTabIdForProcess(nextProcess));
    }, [hiddenTerminalTabIds, terminalProcesses]);
    const projectionContextReferences = sessionProjection?.contextReferences || [];
    const projectionContextGovernanceRaw = sessionProjection?.contextGovernance || null;
    const projectionContextGovernanceHistoryRaw = useMemo(
        () => sessionProjection?.contextGovernanceHistory || [],
        [sessionProjection?.contextGovernanceHistory],
    );
    const projectionContextGovernance = useMemo(
        () => normalizeContextGovernanceDigest(projectionContextGovernanceRaw),
        [projectionContextGovernanceRaw],
    );
    const projectionContextGovernanceHistory = useMemo(
        () => normalizeContextGovernanceHistory(projectionContextGovernanceHistoryRaw),
        [projectionContextGovernanceHistoryRaw],
    );
    const projectionContextUsagePercent = useMemo(
        () => resolveContextUsagePercent(projectionContextGovernance),
        [projectionContextGovernance],
    );
    const projectionRuntimeTimeline = useMemo(
        () => normalizeRuntimeTimeline(sessionProjection?.runtimeTimeline || []),
        [sessionProjection?.runtimeTimeline],
    );
    const projectionMemoryInsight = useMemo(
        () => deriveMemoryRuntimeInsightFromGovernance(
            projectionContextGovernanceRaw,
            projectionContextGovernanceHistoryRaw,
        ),
        [projectionContextGovernanceRaw, projectionContextGovernanceHistoryRaw],
    );
    const runtimeStageModel = useMemo(() => buildRuntimeStageModel(messages, {
        ownerRuntime: sessionProjection?.workflow?.ownerRuntime || sessionProjection?.summary?.ownerRuntime || null,
        status: effectiveStatus || null,
        pendingApproval: effectivePendingApproval,
        recoverable: Boolean(sessionProjection?.recoverable?.recoverable),
        currentStepTitle: sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || null,
        runtimeTimeline: projectionRuntimeTimeline,
        memoryInsight: projectionMemoryInsight,
        governanceDigest: projectionContextGovernance,
        governanceHistory: projectionContextGovernanceHistory,
        locale,
    }), [effectivePendingApproval, effectiveStatus, locale, messages, projectionContextGovernance, projectionContextGovernanceHistory, projectionMemoryInsight, projectionRuntimeTimeline, sessionProjection?.recoverable?.recoverable, sessionProjection?.summary?.currentStepTitle, sessionProjection?.summary?.ownerRuntime, sessionProjection?.workflow?.currentStepTitle, sessionProjection?.workflow?.ownerRuntime]);
    const historyPreview = useMemo(
        () => deriveHistoryPreview(messages, sessionProjection?.summary),
        [messages, sessionProjection?.summary],
    );
    const contentShellClassName = "w-full max-w-[68rem]";
    const greetingText = useMemo(() => {
        const hour = localHour;
        if (locale === "en") {
            if (hour < 12) return "Good morning";
            if (hour < 18) return "Good afternoon";
            return "Good evening";
        }
        if (hour < 12) return "上午好";
        if (hour < 18) return "下午好";
        return "晚上好";
    }, [localHour, locale]);

    useEffect(() => {
        setLocalHour(new Date().getHours());
    }, []);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }
        patchConversationSummary(activeConversationId, {
            lastActivityAt: new Date().toISOString(),
            workflowStatus: effectiveStatus,
            statusLabel: sessionProjection?.summary?.workflowStatus === effectiveStatus
                ? (sessionProjection?.summary as Record<string, unknown> | null)?.statusLabel as string | undefined
                : undefined,
            ownerRuntime: sessionProjection?.workflow?.ownerRuntime || sessionProjection?.summary?.ownerRuntime || undefined,
            currentStepTitle: sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || undefined,
            previewExcerpt: historyPreview,
            lastNarrativeExcerpt: historyPreview,
            lastRuntimeSummary: sessionProjection?.summary && typeof (sessionProjection.summary as Record<string, unknown>).lastRuntimeSummary === "string"
                ? String((sessionProjection.summary as Record<string, unknown>).lastRuntimeSummary)
                : (sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || undefined),
            pendingApprovalCount: effectivePendingApproval
                ? Math.max(
                    Number(governanceApprovals.length || 0),
                    Number(sessionProjection?.controls?.pendingApprovalCount || 0),
                )
                : 0,
            hasPendingApproval: effectivePendingApproval,
            recoverable: Boolean(sessionProjection?.recoverable?.recoverable),
            controls: sessionProjection?.controls || undefined,
        });
    }, [
        activeConversationId,
        effectivePendingApproval,
        effectiveStatus,
        governanceApprovals.length,
        historyPreview,
        patchConversationSummary,
        sessionProjection,
    ]);

    const clearApprovalState = useCallback((options?: { closeModal?: boolean }) => {
        setAskUserApprovalId("");
        setAskUserQuestion("");
        setAskUserToolCallId("");
        setAskUserRequest(null);
        setAskUserCollapsed(false);
        if (options?.closeModal !== false) {
            setAskUserModalOpen(false);
        }
    }, []);

    const applyAskUserPendingApproval = useCallback((approval: {
        id?: string;
        interactionId?: string;
        approvalId?: string;
        approval_id?: string;
        run_id?: string;
        runId?: string;
        approval_kind?: string;
        interactionKind?: string;
        question?: string;
        prompt?: string;
        toolCallId?: string;
        status?: string;
        request?: { question?: string; prompt?: string; toolCallId?: string; approvalKind?: string; interactionKind?: string; approvalId?: string; interactionId?: string; [key: string]: unknown };
    } | null, options?: { openModal?: boolean }) => {
        if (!approval) {
            clearApprovalState();
            return;
        }

        const request = asPlainRecord(approval.request);
        const interactionKind = readString(approval.interactionKind)
            || readString(request.interactionKind)
            || readString(approval.approval_kind)
            || readString(request.approvalKind);
        const approvalId =
            readString(approval.id)
            || readString(approval.interactionId)
            || readString(approval.approvalId)
            || readString(approval.approval_id)
            || readString(request.interactionId)
            || readString(request.approvalId);
        const question =
            readString(approval.question)
            || readString(approval.prompt)
            || readString(request.question)
            || readString(request.prompt);
        const toolCallId = readString(approval.toolCallId) || readString(request.toolCallId);
        const hasAskUserShape = Boolean(approvalId || question || toolCallId);
        if (interactionKind && interactionKind !== "ask_user") {
            clearApprovalState({ closeModal: false });
            return;
        }
        if (!interactionKind && !hasAskUserShape) {
            clearApprovalState({ closeModal: false });
            return;
        }
        if (!(approvalId || toolCallId) || !question) {
            clearApprovalState();
            return;
        }

        setAskUserApprovalId(approvalId);
        setAskUserToolCallId(toolCallId);
        setAskUserQuestion(question);
        setAskUserRequest({
            ...request,
            question,
            prompt: readString(request.prompt) || question,
            toolCallId,
            interactionKind: "ask_user",
        });
        const shouldOpenModal =
            typeof options?.openModal === "boolean"
                ? options.openModal
                : true;
        if (shouldOpenModal) {
            setAskUserCollapsed(false);
            setAskUserModalOpen(true);
        }
    }, [clearApprovalState]);

    useEffect(() => {
        const nextAskUserApprovalId = String(askUserPendingProjection?.id || askUserPendingProjection?.interactionId || "").trim();
        if (!nextAskUserApprovalId) {
            if (askUserApprovalId) {
                clearApprovalState({ closeModal: true });
            }
            return;
        }
        if (nextAskUserApprovalId !== askUserApprovalId) {
            applyAskUserPendingApproval(askUserPendingProjection);
        }
    }, [applyAskUserPendingApproval, askUserApprovalId, askUserPendingProjection, clearApprovalState]);

    const pendingAskUserToolCall = useMemo(
        () => findPendingAskUserToolCall(messages),
        [messages],
    );

    useEffect(() => {
        if (askUserPendingProjection || askUserApprovalId) {
            return;
        }
        if (!pendingAskUserToolCall) {
            if (askUserToolCallId) {
                clearApprovalState({ closeModal: true });
            }
            return;
        }
        if (pendingAskUserToolCall.toolCallId === askUserToolCallId) {
            return;
        }
        applyAskUserPendingApproval({
            toolCallId: pendingAskUserToolCall.toolCallId,
            question: pendingAskUserToolCall.question,
            interactionKind: "ask_user",
            request: pendingAskUserToolCall.request,
        });
    }, [
        applyAskUserPendingApproval,
        askUserApprovalId,
        askUserPendingProjection,
        askUserToolCallId,
        clearApprovalState,
        pendingAskUserToolCall,
    ]);

    useEffect(() => {
        if (!governancePendingApprovalId) {
            setGovernanceApprovalOpen(false);
            if (dismissedGovernanceApprovalId) {
                setDismissedGovernanceApprovalId("");
            }
            return;
        }
        if (dismissedGovernanceApprovalId === governancePendingApprovalId) {
            return;
        }
        setGovernanceApprovalOpen(true);
    }, [dismissedGovernanceApprovalId, governancePendingApprovalId]);

    const openGovernanceApproval = useCallback(() => {
        if (!governancePendingApprovalId) {
            const sessionId = activeConversationIdRef.current;
            if (sessionId) {
                useWorkbenchStore.getState().openDocument(
                    createSessionOverviewDocument(sessionId),
                    { activate: true, mode: "split" },
                );
            }
            return;
        }
        setDismissedGovernanceApprovalId("");
        setGovernanceApprovalOpen(true);
    }, [governancePendingApprovalId]);

    const handleGovernanceApprovalDismiss = useCallback(() => {
        if (governancePendingApprovalId) {
            setDismissedGovernanceApprovalId(governancePendingApprovalId);
        }
        setGovernanceApprovalOpen(false);
    }, [governancePendingApprovalId]);

    const handleGovernanceApprovalViewDetails = useCallback(() => {
        if (governancePendingApprovalId) {
            setDismissedGovernanceApprovalId(governancePendingApprovalId);
        }
        setGovernanceApprovalOpen(false);
        if (governancePendingApproval && isSpecStageApproval(governancePendingApproval as Record<string, unknown>)) {
            const fallbackWorkspacePath = scopeBinding?.workspacePath || mainWorkspacePath || "";
            router.push(buildSpecReviewHref(governancePendingApproval as Record<string, unknown>, fallbackWorkspacePath));
            return;
        }
        const sessionId = activeConversationIdRef.current;
        if (sessionId) {
            useWorkbenchStore.getState().openDocument(
                createSessionOverviewDocument(sessionId),
                { activate: true, mode: "split" },
            );
        }
    }, [governancePendingApproval, governancePendingApprovalId, mainWorkspacePath, router, scopeBinding?.workspacePath]);

    const handleGovernanceApprovalResolve = useCallback(async (answer: string, approve: boolean) => {
        if (!governancePendingApprovalId) {
            return;
        }
        setGovernanceApprovalBusy(true);
        try {
            await resolveApproval(governancePendingApprovalId, answer, approve);
            setGovernanceApprovalOpen(false);
            setDismissedGovernanceApprovalId("");
        } finally {
            setGovernanceApprovalBusy(false);
        }
    }, [governancePendingApprovalId, resolveApproval]);

    useEffect(() => {
        activeConversationIdRef.current = activeConversationId;
    }, [activeConversationId]);

    useEffect(() => {
        isLoadingRef.current = isLoading;
    }, [isLoading]);

    useEffect(() => {
        messagesRef.current = messages;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            messages,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
    }, [messages]);

    const isLocalStreamActive = useCallback((sessionId: string | null | undefined) => {
        if (!sessionId) return false;
        return isLoadingRef.current && streamingConversationIdRef.current === sessionId;
    }, []);

    const normalizeTurnPageMessages = useCallback((items: unknown[]) => {
        const hasTimelineNodes = items.some((message: unknown) =>
            Boolean(message && typeof message === "object" && Array.isArray((message as { nodes?: unknown[] }).nodes)),
        );
        return hasTimelineNodes
            ? normalizeMessagesForState(items as Message[])
            : normalizeProjectedMessages(items);
    }, []);

    const loadConversationTurnPage = useCallback(async (
        conversationId: string,
        options?: { before?: string | null },
    ) => {
        const params = new URLSearchParams({ limit: "1" });
        params.set("surface", "web");
        params.set("compact", "1");
        const before = String(options?.before || "").trim();
        if (before) {
            params.set("before", before);
        }
        const turnsRes = await fetch(`/api/conversations/${conversationId}/turns?${params.toString()}`, {
            cache: "no-store",
        });
        if (!turnsRes.ok) {
            throw new Error(`Failed to load conversation turns: ${turnsRes.status}`);
        }
        const turnsPayload = await turnsRes.json().catch(() => ({}));
        const items = Array.isArray(turnsPayload?.messages) ? turnsPayload.messages : [];
        const pageInfo = (turnsPayload?.pageInfo && typeof turnsPayload.pageInfo === "object") ? turnsPayload.pageInfo : {};
        return {
            messages: normalizeTurnPageMessages(items),
            pageInfo: {
                hasMore: Boolean(pageInfo.hasMore),
                beforeCursor: pageInfo.beforeCursor == null ? null : String(pageInfo.beforeCursor),
                loadedTurnCount: Number(pageInfo.loadedTurnCount || 0),
            },
        };
    }, [normalizeTurnPageMessages]);

    const applyProjectedSnapshot = useCallback((projectedMessages: unknown[], latestSeq = 0) => {
        if (runtimeFlushFrameRef.current !== null && typeof window !== "undefined") {
            window.cancelAnimationFrame(runtimeFlushFrameRef.current);
            runtimeFlushFrameRef.current = null;
        }
        if (runtimeFlushTimerRef.current) {
            clearTimeout(runtimeFlushTimerRef.current);
            runtimeFlushTimerRef.current = null;
        }
        const normalized = mergeProjectedSnapshotMessages(messagesRef.current, projectedMessages);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            normalized,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        latestRealtimeSeqRef.current = latestSeq;
        messagesRef.current = normalizeMessagesForState(normalized);
        setMessages(normalizeMessagesForState(normalized));
        return normalized;
    }, [setMessages]);

    const applySessionProcessSurface = useCallback((incoming: AdminProcessRef[], options?: { forceClear?: boolean }) => {
        const normalizedIncoming = dedupeProcesses(incoming || []);
        setSessionProcessSurface((current) => {
            if (normalizedIncoming.length > 0) {
                lastSessionProcessSurfaceAtRef.current = Date.now();
                return normalizedIncoming;
            }
            if (options?.forceClear) {
                lastSessionProcessSurfaceAtRef.current = 0;
                return [];
            }
            if (current.length === 0) {
                return current;
            }
            return (Date.now() - lastSessionProcessSurfaceAtRef.current) <= 3000 ? current : [];
        });
    }, []);

    const loadConversationHistory = useCallback(async (conversationId: string) => {
        const detailRes = await fetch(`/api/conversations/${conversationId}/detail?omitMessages=1`, { cache: "no-store" });
        if (!detailRes.ok) {
            if (detailRes.status === 404) {
                router.replace("/chat");
                return;
            }
            throw new Error(`Failed to load conversation detail: ${detailRes.status}`);
        }

        const data = await detailRes.json();
        const detailPayload = (data?.payload && typeof data.payload === "object") ? data.payload : data;
        const projectionPayload = (detailPayload?.projection && typeof detailPayload.projection === "object")
            ? detailPayload.projection
            : detailPayload;
        setLegacyChatUnsupported(isLegacyChatUnsupportedPayload(detailPayload) || isLegacyChatUnsupportedPayload(projectionPayload));
        applyQueuedMessagesSnapshot(extractQueuedMessages(projectionPayload) ?? extractQueuedMessages(detailPayload));
        const projection = deriveAuthoritativeSessionView(projectionPayload).view as SessionProjectionView | null;
        setSessionProjection(projection);
        if (projection?.askUserInteractions?.length) {
            const askUserInteraction = projection.askUserInteractions.find((item) => String(item.status || "pending").toLowerCase() === "pending") || null;
            if (askUserInteraction) {
                const nextAskUserApprovalId = readString(askUserInteraction.id) || readString(askUserInteraction.interactionId);
                applyAskUserPendingApproval(askUserInteraction, { openModal: !askUserApprovalId || nextAskUserApprovalId !== askUserApprovalId });
            }
        }

        const latestSeq = Number(projectionPayload?.latestSeq || projectionPayload?.snapshot?.latest_seq || 0);
        const turnPage = await loadConversationTurnPage(conversationId);
        const normalized = normalizeMessagesForState(turnPage.messages);
        historyPagingModeRef.current = true;
        turnBeforeCursorRef.current = turnPage.pageInfo.beforeCursor;
        isLoadingOlderTurnsRef.current = false;
        setIsLoadingOlderTurns(false);
        setHasOlderTurns(Boolean(turnPage.pageInfo.hasMore));
        latestRealtimeSeqRef.current = latestSeq;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            normalized,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        messagesRef.current = normalized;
        setMessages(normalized);
        const detailProcesses = Array.isArray(detailPayload?.processes) ? detailPayload.processes : [];
        if (detailProcesses.length > 0) {
            applySessionProcessSurface(detailProcesses);
        }
    }, [applyAskUserPendingApproval, applyQueuedMessagesSnapshot, applySessionProcessSurface, askUserApprovalId, loadConversationTurnPage, router, setMessages]);

    const loadOlderConversationTurn = useCallback(async () => {
        const conversationId = activeConversationIdRef.current;
        const before = turnBeforeCursorRef.current;
        if (!conversationId || !before || !hasOlderTurns || isLoadingOlderTurnsRef.current) {
            return;
        }
        isLoadingOlderTurnsRef.current = true;
        setIsLoadingOlderTurns(true);
        try {
            const turnPage = await loadConversationTurnPage(conversationId, { before });
            const incoming = normalizeMessagesForState(turnPage.messages);
            const seen = new Set(incoming.map((message) => String(message.id || "")));
            const nextMessages = normalizeMessagesForState([
                ...incoming,
                ...messagesRef.current.filter((message) => !seen.has(String(message.id || ""))),
            ]);
            turnBeforeCursorRef.current = turnPage.pageInfo.beforeCursor;
            setHasOlderTurns(Boolean(turnPage.pageInfo.hasMore));
            messagesRef.current = nextMessages;
            realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                nextMessages,
                WEB_STREAM_LIFECYCLE_OPTIONS,
            );
            setMessages(nextMessages);
        } catch (error) {
            console.warn("[ChatClient] Failed to load older conversation turn:", error);
        } finally {
            isLoadingOlderTurnsRef.current = false;
            setIsLoadingOlderTurns(false);
        }
    }, [hasOlderTurns, loadConversationTurnPage, setMessages]);

    const loadProjects = useCallback(async () => {
        setProjectsLoading(true);
        try {
            const res = await fetch("/api/projects", { cache: "no-store" });
            if (!res.ok) {
                return;
            }
            const data = await res.json();
            const nextProjects = Array.isArray(data?.projects) ? data.projects : [];
            const nextMainWorkspacePath = typeof data?.mainWorkspacePath === "string" ? data.mainWorkspacePath : "";
            setProjects(nextProjects);
            setMainWorkspacePath(nextMainWorkspacePath);
        } catch (error) {
            console.warn("[ChatClient] Failed to load projects:", error);
        } finally {
            setProjectsLoading(false);
        }
    }, []);

    const loadSessionScope = useCallback(async (conversationId: string) => {
        setScopeLoading(true);
        try {
            const res = await fetch(`/api/sessions/${conversationId}/scope`, { cache: "no-store" });
            if (!res.ok) {
                setScopeBinding(null);
                return;
            }
            const data = await res.json();
            const normalized = normalizeScopeBinding(data?.binding);
            setScopeBinding(normalized);
        } catch (error) {
            console.warn("[ChatClient] Failed to load scope binding:", error);
            setScopeBinding(null);
        } finally {
            setScopeLoading(false);
        }
    }, []);

    const loadSessionProcesses = useCallback(async (conversationId: string) => {
        try {
            const res = await fetch(`/api/sessions/${encodeURIComponent(conversationId)}/processes`, { cache: "no-store" });
            if (!res.ok) {
                applySessionProcessSurface([]);
                return;
            }
            const data = await res.json().catch(() => ({}));
            applySessionProcessSurface(Array.isArray(data?.processes) ? data.processes : []);
        } catch (error) {
            console.warn("[ChatClient] Failed to load session processes:", error);
            applySessionProcessSurface([]);
        }
    }, [applySessionProcessSurface]);

    useEffect(() => {
        if (!activeConversationId) {
            applySessionProcessSurface([], { forceClear: true });
            return;
        }

        applySessionProcessSurface([], { forceClear: true });
        void loadSessionProcesses(activeConversationId);
        const timer = window.setInterval(() => {
            void loadSessionProcesses(activeConversationId);
        }, 1800);

        return () => {
            window.clearInterval(timer);
        };
    }, [activeConversationId, applySessionProcessSurface, loadSessionProcesses]);

    const buildScopePayload = useCallback((conversationId?: string | null) => ({
        conversationId: conversationId || activeConversationIdRef.current || undefined,
        projectId: scopeBinding?.projectId || undefined,
        workspaceId: scopeBinding?.workspaceId || undefined,
        workspacePath: scopeBinding?.workspacePath || undefined,
        scopeHint: scopeBinding?.resolvedScope || undefined,
        scopeMode: "explicit",
    }), [scopeBinding?.projectId, scopeBinding?.resolvedScope, scopeBinding?.workspaceId, scopeBinding?.workspacePath]);

    const submitQueuedMessage = useCallback(async (
        content: string,
        data?: Record<string, unknown>,
    ) => {
        const conversationId = activeConversationIdRef.current;
        if (!conversationId) {
            return;
        }

        const requestData: Record<string, unknown> = {
            agentId: undefined,
            userId: session?.user?.id,
            ...buildScopePayload(conversationId),
            ...(data || {}),
        };
        const dataAttachments: Record<string, unknown>[] = Array.isArray(requestData.attachments)
            ? requestData.attachments.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
            : [];
        const allFileUrls = Array.isArray(requestData.fileUrls)
            ? requestData.fileUrls.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
            : [];
        const requestMessages = [
            ...messagesRef.current.map((message) => ({ role: message.role, content: message.content })),
            { role: "user", content },
        ];
        const requestBody: Record<string, unknown> = {
            messages: requestMessages,
            data: requestData,
            fileUrls: allFileUrls,
            attachments: dataAttachments,
            session_id: conversationId,
            conversationId,
            project_id: requestData.projectId ?? requestData.project_id,
            workspace_id: requestData.workspaceId ?? requestData.workspace_id,
            workspace_path: requestData.workspacePath ?? requestData.workspace_path,
            thread_id: requestData.threadId ?? requestData.thread_id,
            scope_hint: requestData.scopeHint ?? requestData.scope_hint,
            scope_mode: requestData.scopeMode ?? requestData.scope_mode ?? "explicit",
        };

        const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(requestBody),
        });
        const responseText = await response.text();
        let payload: ChatQueueSubmitResponse = {};
        if (responseText.trim()) {
            try {
                payload = JSON.parse(responseText) as ChatQueueSubmitResponse;
            } catch {
                payload = {};
            }
        }
        if (!response.ok) {
            throw new Error(readErrorPayloadMessage(payload as Record<string, unknown>) || `Queue request failed: ${response.status}`);
        }
        if (payload.queued && payload.queuedMessage) {
            upsertQueuedMessage(payload.queuedMessage);
            setQueuedMessagesCollapsed(false);
            setQueuedMessageError("");
            return;
        }
        if (payload.queued) {
            setQueuedMessagesCollapsed(false);
            setQueuedMessageError("");
            return;
        }
        throw new Error(t("web.generated.0bf47da6e3"));
    }, [buildScopePayload, session?.user?.id, t, upsertQueuedMessage]);

    const handlePromoteQueuedMessage = useCallback(async (item: QueuedChatMessage) => {
        const id = String(item.id || "").trim();
        if (!id || queuedMessageBusyId) {
            return;
        }
        setQueuedMessageBusyId(id);
        setQueuedMessageError("");
        setQueuedMessageMenuId(null);
        try {
            const response = await fetch(`/api/chat-queue/${encodeURIComponent(id)}/promote`, { method: "POST" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload?.ok === false) {
                throw new Error(readString(payload?.error) || readString(payload?.detail));
            }
            upsertQueuedMessage(payload?.queuedMessage || { ...item, state: "promoted" });
        } catch (error) {
            console.error("[ChatClient] Failed to promote queued message:", error);
            setQueuedMessageError(error instanceof Error && error.message ? error.message : t("web.generated.8f1e4072ac"));
        } finally {
            setQueuedMessageBusyId("");
        }
    }, [queuedMessageBusyId, t, upsertQueuedMessage]);

    const handleCancelQueuedMessage = useCallback(async (item: QueuedChatMessage) => {
        const id = String(item.id || "").trim();
        if (!id || queuedMessageBusyId) {
            return;
        }
        setQueuedMessageBusyId(id);
        setQueuedMessageError("");
        setQueuedMessageMenuId(null);
        try {
            const response = await fetch(`/api/chat-queue/${encodeURIComponent(id)}`, { method: "DELETE" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload?.ok === false) {
                throw new Error(readString(payload?.error) || readString(payload?.detail));
            }
            upsertQueuedMessage(payload?.queuedMessage || { ...item, state: "cancelled" });
        } catch (error) {
            console.error("[ChatClient] Failed to cancel queued message:", error);
            setQueuedMessageError(error instanceof Error && error.message ? error.message : t("web.generated.5c2e41d9a8"));
        } finally {
            setQueuedMessageBusyId("");
        }
    }, [queuedMessageBusyId, t, upsertQueuedMessage]);

    const handleOpenQueuedMessageEditor = useCallback((item: QueuedChatMessage) => {
        const state = String(item.state || "pending").trim().toLowerCase();
        if (state !== "pending") {
            return;
        }
        setQueuedMessageMenuId(null);
        setEditingQueuedMessage(item);
        setQueuedMessageEditText(String(item.content || ""));
    }, []);

    const handleSaveQueuedMessageEdit = useCallback(async () => {
        const item = editingQueuedMessage;
        const id = String(item?.id || "").trim();
        const nextContent = queuedMessageEditText.trim();
        if (!id || !item || !nextContent || queuedMessageEditBusy) {
            return;
        }
        setQueuedMessageEditBusy(true);
        setQueuedMessageError("");
        try {
            const response = await fetch(`/api/chat-queue/${encodeURIComponent(id)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: nextContent }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload?.ok === false) {
                throw new Error(readString(payload?.error) || readString(payload?.detail));
            }
            upsertQueuedMessage(payload?.queuedMessage || { ...item, content: nextContent, state: "pending" });
            setEditingQueuedMessage(null);
            setQueuedMessageEditText("");
        } catch (error) {
            console.error("[ChatClient] Failed to edit queued message:", error);
            setQueuedMessageError(error instanceof Error && error.message ? error.message : t("web.generated.76ac182bf4"));
        } finally {
            setQueuedMessageEditBusy(false);
        }
    }, [editingQueuedMessage, queuedMessageEditBusy, queuedMessageEditText, t, upsertQueuedMessage]);

    const clearNewConversationIntent = useCallback(() => {
        if (typeof window === "undefined") {
            return;
        }
        window.history.replaceState(null, "", "/chat");
    }, []);

    const createBoundConversation = useCallback(async (draft: WorkspaceBindingDraft) => {
        if (workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            let creationPayload: CreateConversationPayload = {
                title: "New Chat",
                scopeMode: "explicit",
            };
            if (draft.kind === "main") {
                creationPayload = {
                    ...creationPayload,
                    workspacePath: mainWorkspacePath || undefined,
                    scopeHint: "global",
                };
            } else {
                const project = projects.find((item) => item.id === draft.projectId);
                if (!project?.id) {
                    throw new Error("Project not found");
                }
                creationPayload = {
                    ...creationPayload,
                    projectId: project.id,
                    workspaceId: project.workspaceId,
                    workspacePath: project.workspacePath,
                    scopeHint: project.defaultScope,
                };
            }
            const newConversation = await createConversation(creationPayload);
            if (!newConversation?.id) {
                throw new Error("Conversation creation failed");
            }
            contextTakeoverConversationIdRef.current = pendingContextSessionRefs.length > 0
                ? newConversation.id
                : null;
            activeConversationIdRef.current = newConversation.id;
            setActiveConversationId(newConversation.id);
            setWorkspaceChooserVisible(false);
            setNewProjectPath("");
            window.history.replaceState(null, "", `/chat?id=${newConversation.id}`);
            await loadSessionScope(newConversation.id);
            await refreshConversations();
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [createConversation, loadSessionScope, mainWorkspacePath, pendingContextSessionRefs.length, projects, refreshConversations, workspaceChooserBusy]);

    const handleCreateProjectConversation = useCallback(async () => {
        const trimmedPath = newProjectPath.trim();
        if (!trimmedPath || workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            const createProjectAtPath = async (trusted: boolean) => {
                const res = await fetch("/api/projects", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        workspacePath: trimmedPath,
                        ...(trusted ? {
                            workspaceTrustState: "trusted",
                            workspaceTrustSource: "user_confirmed",
                        } : {}),
                    }),
                });
                const payload = await res.json().catch(() => ({}));
                if (!res.ok) {
                    return { ok: false, status: res.status, payload };
                }
                return { ok: true, status: res.status, payload };
            };
            let result = await createProjectAtPath(false);
            if (!result.ok && result.status === 400 && isWorkspaceTrustRequiredPayload(result.payload as Record<string, unknown>)) {
                const confirmed = window.confirm(t("web.chat.workspaceTrust.confirmExternal", { value0: trimmedPath }));
                if (!confirmed) {
                    return;
                }
                result = await createProjectAtPath(true);
            }
            if (!result.ok) {
                const payload = result.payload as Record<string, unknown>;
                throw new Error(String(payload.detail || payload.error || `Project creation failed: ${result.status}`));
            }
            const createdProject = result.payload as ProjectDescriptor;
            await loadProjects();
            const creationPayload: CreateConversationPayload = {
                title: "New Chat",
                projectId: createdProject?.id,
                workspaceId: createdProject?.workspaceId,
                workspacePath: createdProject?.workspacePath,
                scopeHint: createdProject?.defaultScope,
                scopeMode: "explicit",
            };
            const newConversation = await createConversation(creationPayload);
            if (!newConversation?.id) {
                throw new Error("Conversation creation failed");
            }
            contextTakeoverConversationIdRef.current = pendingContextSessionRefs.length > 0
                ? newConversation.id
                : null;
            activeConversationIdRef.current = newConversation.id;
            setActiveConversationId(newConversation.id);
            setWorkspaceChooserVisible(false);
            setNewProjectPath("");
            window.history.replaceState(null, "", `/chat?id=${newConversation.id}`);
            await loadSessionScope(newConversation.id);
            await refreshConversations();
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [createConversation, loadProjects, loadSessionScope, newProjectPath, pendingContextSessionRefs.length, refreshConversations, t, workspaceChooserBusy]);

    useEffect(() => {
        if (status === "authenticated") {
            void loadProjects();
            return;
        }
        if (status === "unauthenticated") {
            setProjects([]);
            setMainWorkspacePath("");
            setWorkspaceChooserVisible(false);
            setProjectsLoading(false);
        }
    }, [loadProjects, status]);

    useEffect(() => {
        if (status !== "unauthenticated" || localConnectAttemptedRef.current) {
            return;
        }
        localConnectAttemptedRef.current = true;
        setLocalConnectError(null);

        let cancelled = false;
        void (async () => {
            try {
                const connectionResponse = await fetch("/api/connection", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ adminBaseUrl: DEFAULT_LOCAL_ADMIN_BASE_URL, persist: true }),
                });
                if (!connectionResponse.ok) {
                    const payload = await connectionResponse.json().catch(() => null);
                    throw new Error(payload?.error || payload?.message || t("web.generated.40132fa524"));
                }

                const result = await signIn("credentials", {
                    localSession: "1",
                    adminBaseUrl: DEFAULT_LOCAL_ADMIN_BASE_URL,
                    redirect: false,
                });
                if (result?.error) {
                    throw new Error(result.error);
                }
                if (!cancelled) {
                    router.refresh();
                }
            } catch (error) {
                if (!cancelled) {
                    setLocalConnectError(error instanceof Error ? error.message : t("web.generated.ecfd08ab82"));
                }
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [router, status, t]);

    useEffect(() => {
        if (status !== "authenticated") {
            setSupervisorReasoningEffortControl(null);
            return;
        }
        let cancelled = false;
        void fetch("/api/models/supervisor-reasoning-effort", { cache: "no-store" })
            .then((res) => res.ok ? res.json() : null)
            .then((payload) => {
                if (!cancelled) {
                    setSupervisorReasoningEffortControl(payload && typeof payload === "object" ? payload : null);
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setSupervisorReasoningEffortControl(null);
                }
            });
        return () => {
            cancelled = true;
        };
    }, [status]);

    useEffect(() => {
        if (activeConversationId) {
            setWorkspaceChooserVisible(false);
            return;
        }
        if (newConversationIntent) {
            setWorkspaceChooserVisible(true);
        }
    }, [activeConversationId, newConversationIntent]);

    const applyRemoteRuntimeEvent = useCallback((rawEvent: unknown) => {
        const conversationId = activeConversationIdRef.current;
        if (!conversationId) {
            return;
        }

        const runtimeTimelineEntry = buildRuntimeTimelineEntryFromEvent(rawEvent);
        const workbenchEventHandled = ingestWorkbenchRuntimeEvent(rawEvent);
        const normalizedEvent = normalizeRealtimeEvent(rawEvent);
        if (!normalizedEvent) {
            if (workbenchEventHandled) {
                return;
            }
            return;
        }
        const isHumanGuidanceEvent =
            normalizedEvent.name === "human_guidance"
            || String(normalizedEvent.topic || "").startsWith("human_guidance.");
        const isSessionCoordinationEvent =
            normalizedEvent.name === "session_coordination"
            || String(normalizedEvent.topic || "").startsWith("session_coordination.");

        const localStreamActive = isLocalStreamActive(conversationId);
        if (
            localStreamActive
            && !runtimeTimelineEntry
            && !(normalizedEvent.type === "custom_event" && normalizedEvent.name === "artifact_recorded")
            && !isHumanGuidanceEvent
            && !isSessionCoordinationEvent
        ) {
            return;
        }

        if (normalizedEvent.type === "custom_event" && normalizedEvent.name === "ask_user") {
            const eventData = typeof normalizedEvent.data === "object" && normalizedEvent.data !== null
                ? normalizedEvent.data as Record<string, unknown>
                : {};
            const request = asPlainRecord(eventData.request);
            const interactionId = readString(eventData.interactionId) || readString(eventData.id) || readString(eventData.approvalId);
            applyAskUserPendingApproval({
                id: interactionId,
                interactionId,
                approvalId: readString(eventData.approvalId),
                run_id: String((normalizedEvent as Record<string, unknown>).run_id || ""),
                interactionKind: "ask_user",
                question: readString(eventData.question),
                toolCallId: readString(eventData.toolCallId),
                request: {
                    ...request,
                    question: readString(request.question) || readString(eventData.question),
                    prompt: readString(request.prompt) || readString(eventData.prompt) || readString(eventData.question),
                    toolCallId: readString(request.toolCallId) || readString(eventData.toolCallId),
                    interactionKind: "ask_user",
                },
            });
        }

        if (isHumanGuidanceEvent) {
            const eventData = asPlainRecord(normalizedEvent.data);
            const queueMessage = normalizeQueuedMessage(eventData.queueMessage);
            const queueId =
                readString(eventData.queueMessageId)
                || readString(eventData.guidanceQueueMessageId)
                || queueMessage?.id
                || "";
            const queueState = readString(eventData.state).toLowerCase();
            const terminalEvent = ["human_guidance.injected", "human_guidance.consumed", "human_guidance.cancelled"].includes(String(normalizedEvent.topic || ""))
                || ["injected", "consumed", "cancelled"].includes(queueState);
            if (queueId && terminalEvent) {
                setQueuedMessages((current) => current.filter((item) => item.id !== queueId));
            } else if (queueMessage) {
                upsertQueuedMessage(queueMessage);
            }
        }

        if (
            normalizedEvent.topic === "approval.approved"
            || normalizedEvent.topic === "approval.rejected"
            || normalizedEvent.topic === "ask_user.resolved"
        ) {
            clearApprovalState();
        }

        const trackedTopics = new Set([
            "run.state.changed",
            "run.completed",
            "run.failed",
            "run.cancelled",
            "run.paused",
            "run.resumed",
            "run.interrupted",
            "run.retry.requested",
            "ask_user.requested",
            "ask_user.resolved",
            "approval.requested",
            "approval.approved",
            "approval.rejected",
        ]);
        if (normalizedEvent.topic && trackedTopics.has(String(normalizedEvent.topic))) {
            void loadRuns(conversationId);
        }

        const rawSeq = typeof rawEvent === "object" && rawEvent !== null
            ? Number((rawEvent as Record<string, unknown>).seq || 0)
            : 0;
        const normalizedSeq = Number((normalizedEvent as Record<string, unknown>).seq || 0);
        const eventSeq = rawSeq || normalizedSeq;
        if (eventSeq && eventSeq <= latestRealtimeSeqRef.current) {
            return;
        }

        if (eventSeq) {
            latestRealtimeSeqRef.current = eventSeq;
        }

        if (runtimeTimelineEntry) {
            setSessionProjection((current) => {
                if (!current) {
                    return current;
                }
                return {
                    ...current,
                    runtimeTimeline: mergeRuntimeTimeline(
                        normalizeRuntimeTimeline(current.runtimeTimeline || []),
                        [runtimeTimelineEntry],
                    ),
                };
            });
        }
        const pendingDiagnostic = recordReceivedStreamDelta({
            surface: "web/realtime",
            event: normalizedEvent,
            diagnostics: readStreamDiagnostics(rawEvent),
            receivedAtMs: Date.now(),
            statsByKey: streamLatencyStatsRef.current,
        });
        if (pendingDiagnostic) {
            pendingStreamDiagnosticRef.current = pendingDiagnostic;
        }
        queueSessionRealtimeRuntimeEvent(realtimeMessageStateRef.current, normalizedEvent);

        const flush = () => {
            runtimeFlushFrameRef.current = null;
            runtimeFlushTimerRef.current = null;
            const nextState = flushQueuedSessionRealtimeRuntimeEvents(
                messagesRef.current,
                realtimeMessageStateRef.current,
                {
                    cloneMessages,
                    normalizeMessages: normalizeMessagesForState,
                    lifecycleOptions: WEB_STREAM_LIFECYCLE_OPTIONS,
                },
            );
            realtimeMessageStateRef.current = nextState.state;
            if (!nextState.changed) {
                return;
            }

            messagesRef.current = nextState.messages;
            setMessages(nextState.messages);
            const pendingStreamDiagnostic = pendingStreamDiagnosticRef.current;
            pendingStreamDiagnosticRef.current = null;
            if (pendingStreamDiagnostic) {
                const committedAtMs = Date.now();
                markStreamClientCommit(streamLatencyStatsRef.current, pendingStreamDiagnostic, committedAtMs);
                const markRendered = () => {
                    markStreamClientRender(streamLatencyStatsRef.current, pendingStreamDiagnostic, Date.now());
                };
                if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
                    window.requestAnimationFrame(markRendered);
                } else {
                    setTimeout(markRendered, 0);
                }
            }
        };

        if (runtimeFlushFrameRef.current !== null || runtimeFlushTimerRef.current) {
            return;
        }

        if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
            runtimeFlushFrameRef.current = window.requestAnimationFrame(flush);
        } else {
            runtimeFlushTimerRef.current = setTimeout(flush, 16);
        }
    }, [applyAskUserPendingApproval, clearApprovalState, isLocalStreamActive, loadRuns, setMessages, upsertQueuedMessage]);

    useEffect(() => {
        const streamLatencyStats = streamLatencyStatsRef.current;
        const realtimeMessageState = realtimeMessageStateRef.current;
        return () => {
            if (runtimeFlushFrameRef.current !== null && typeof window !== "undefined") {
                window.cancelAnimationFrame(runtimeFlushFrameRef.current);
            }
            if (runtimeFlushTimerRef.current) {
                clearTimeout(runtimeFlushTimerRef.current);
            }
            streamLatencyStats.clear();
            realtimeMessageState.pendingRuntimeEvents = [];
        };
    }, []);

    const handleAskUserSubmit = async (answer: string, approve: boolean) => {
        try {
            if (askUserApprovalId) {
                if (approve) {
                    const response = await fetch(`/api/ask-user/${encodeURIComponent(askUserApprovalId)}/respond`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ answer, response: { answer } }),
                    });
                    if (!response.ok) {
                        throw new Error(`ask_user respond failed: ${response.status}`);
                    }
                }
            } else if (approve) {
                await sendToolOutput(askUserToolCallId, answer, buildScopePayload(activeConversationId));
            }
            clearApprovalState();
            if (activeConversationIdRef.current) {
                void loadRuns(activeConversationIdRef.current);
            }
        } catch (error) {
            console.error("[ChatClient] Failed to resolve ask_user request:", error);
        }
    };

    // Handle New Message Sound Effect
    useEffect(() => {
        if (messages.length === 0) return;

        const latestMsg = messages[messages.length - 1];

        // 1. Filter out user messages & system messages
        if (latestMsg.role !== 'assistant') {
            lastMessageIdRef.current = latestMsg.id;
            lastMessageLengthRef.current = latestMsg.nodes?.length || 0; // Approximate
            return;
        }

        // 2. Identify if this is a NEW message (ID changed)
        const isNewMessageObject = latestMsg.id !== lastMessageIdRef.current;

        if (isNewMessageObject) {
            // New message object detected.
            // If it HAS content immediately, it's likely history loading (OR a very fast full response).
            // But usually history loading brings full content.
            // To be safe: If it's history, we usually setMessages with MANY messages.
            // Check if we are currently loading history? No, relying on state is tricky.
            // Heuristic: If content length > 0 immediately on first sight, Assume History/Snapshot. 
            // Real streaming starts with empty string usually.

            // However, our optimistic UI adds an empty placeholder `currentAiMsg` first.
            // So for streaming, we see: ID_NEW, Length 0. -> Then Length > 0.

            lastMessageIdRef.current = latestMsg.id;
            lastMessageLengthRef.current = String(latestMsg.content || "").length;
        } else {
            // Same message object, content updating.
            const currentLength = String(latestMsg.content || "").length;
            const previousLength = lastMessageLengthRef.current;

            // 3. Trigger Sound: If length transitions from 0 to > 0
            if (previousLength === 0 && currentLength > 0) {
                // Check if it's "智能主管" (Supervisor) or actual Agent
                // We might want sound for both.
                // Play Sound!
                audioRef.current?.play().catch(e => console.error("Audio play failed", e));
            }

            lastMessageLengthRef.current = currentLength;
        }

    }, [messages]);

    // Handle Input Change
    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        setInput(e.target.value);
    };

    // Handle Send
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleSend = async (e: React.FormEvent<HTMLFormElement>, options?: { data?: any }) => {
        e.preventDefault();
        const optionData = { ...(options?.data || {}) };
        const messageOverride = typeof optionData.messageOverride === "string" ? optionData.messageOverride : null;
        delete optionData.messageOverride;
        const currentInput = messageOverride ?? input;
        const hasText = currentInput.trim().length > 0;
        const hasCommandPreset = Boolean(optionData.commandPreset?.name);
        const hasSkillReferences = Array.isArray(optionData.skillReferences) && optionData.skillReferences.length > 0;
        const hasFiles = Array.isArray(optionData.fileUrls) && optionData.fileUrls.length > 0;
        if (status !== 'authenticated' || (!hasText && !hasCommandPreset && !hasSkillReferences && !hasFiles)) return false;
        if (!activeConversationIdRef.current) {
            setWorkspaceChooserVisible(true);
            if (!newConversationIntent) {
                clearNewConversationIntent();
            }
            return false;
        }

        const submissionData = {
            ...optionData,
            ...(pendingContextSessionRefs.length > 0 ? { contextSessionRefs: pendingContextSessionRefs } : {}),
        };
        if (messageOverride === null) setInput(""); // Clear the visible Composer only for Composer submissions.
        if (activeConversationRunning) {
            try {
                await submitQueuedMessage(currentInput, submissionData);
                clearPendingContextSessionRefs();
                return true;
            } catch (error) {
                console.error("[ChatClient] Failed to queue message:", error);
                const errorMessage = error instanceof Error && error.message ? error.message : t("web.generated.38c9a5e21f");
                if (isWorkspaceBindingErrorMessage(errorMessage)) {
                    setWorkspaceChooserVisible(true);
                }
                if (messageOverride === null) setInput(currentInput);
                setQueuedMessageError(errorMessage);
                return false;
            }
        }

        historyPagingModeRef.current = false;
        turnBeforeCursorRef.current = null;
        isLoadingOlderTurnsRef.current = false;
        setHasOlderTurns(false);
        setIsLoadingOlderTurns(false);

        // [REMOVED] Optimistic UI: The useLangGraphStream hook now handles both User and AI placeholders internally.
        // This prevents the "Flicker" caused by state conflicts (Client vs Hook)

        try {
            const accepted = await sendMessage(currentInput, {
                agentId: undefined, // selectedAgent?.id,
                userId: session?.user?.id,
                ...buildScopePayload(activeConversationIdRef.current),
                ...submissionData,
            });
            if (accepted) {
                clearPendingContextSessionRefs();
            } else {
                if (messageOverride === null) setInput(currentInput);
            }
            return Boolean(accepted);
        } catch (error) {
            console.error("[ChatClient] Failed to send initial message:", error);
            const errorMessage = error instanceof Error && error.message ? error.message : "";
            if (isWorkspaceBindingErrorMessage(errorMessage)) {
                setWorkspaceChooserVisible(true);
                setQueuedMessageError(errorMessage);
            }
            if (messageOverride === null) setInput(currentInput);
            return false;
        }
    };

    const handleFileLineComment = async (reference: { path: string; line: number; lineText: string; comment: string }) => {
        const quotedLine = String(reference.lineText || "").replace(/\r?\n/g, " ").trim() || "（空行）";
        const message = [
            reference.comment.trim(),
            "",
            `文件定位：\`${reference.path}:${reference.line}\``,
            `第 ${reference.line} 行：\`${quotedLine.replace(/`/g, "\\`")}\``,
        ].join("\n");
        const syntheticEvent = { preventDefault() {} } as React.FormEvent<HTMLFormElement>;
        return Boolean(await handleSend(syntheticEvent, { data: { messageOverride: message } }));
    };

    const handleVoiceAudioMessage = (data: { fileUrls: string[]; attachments: Array<Record<string, unknown>>; safetyApprovalMode?: "manual" | "reduced" | "minimal" }) => {
        const hasFiles = Array.isArray(data.fileUrls) && data.fileUrls.length > 0;
        if (status !== 'authenticated' || !hasFiles) return;
        if (!activeConversationIdRef.current) {
            setWorkspaceChooserVisible(true);
            if (!newConversationIntent) {
                clearNewConversationIntent();
            }
            return;
        }
        const submissionData = {
            ...data,
            ...(pendingContextSessionRefs.length > 0 ? { contextSessionRefs: pendingContextSessionRefs } : {}),
        };
        if (activeConversationRunning) {
            void submitQueuedMessage("", submissionData).then(() => {
                clearPendingContextSessionRefs();
            }).catch((error) => {
                console.error("[ChatClient] Failed to queue voice audio message:", error);
                const errorMessage = error instanceof Error && error.message ? error.message : t("web.generated.38c9a5e21f");
                if (isWorkspaceBindingErrorMessage(errorMessage)) {
                    setWorkspaceChooserVisible(true);
                }
                setQueuedMessageError(errorMessage);
            });
            return;
        }

        historyPagingModeRef.current = false;
        turnBeforeCursorRef.current = null;
        isLoadingOlderTurnsRef.current = false;
        setHasOlderTurns(false);
        setIsLoadingOlderTurns(false);

        void sendMessage("", {
            agentId: undefined,
            userId: session?.user?.id,
            ...buildScopePayload(activeConversationIdRef.current),
            ...submissionData,
        }).then((accepted) => {
            if (accepted) {
                clearPendingContextSessionRefs();
            }
        });
    };

    // Fetch history when ID changes
    useEffect(() => {
        if (activeConversationId) {
            // CRITICAL FIX: If we are currently streaming content for this ID, 
            // DO NOT fetch from DB. The DB history is stale (empty) compared to our live stream.
            // Fetching would overwrite our live state with empty history, causing the "Flicker/Disappear" bug.
            if (isLoading && streamingConversationIdRef.current === activeConversationId) {
                console.log(`[ChatClient] Skipping history fetch for ${activeConversationId} (Streaming active)`);
                return;
            }
            console.log(`[ChatClient] Fetching history for ${activeConversationId}`);
            void loadConversationHistory(activeConversationId).catch((err) => {
                console.error("Failed to load chat history", err);
            });
            void loadSessionScope(activeConversationId);
            void loadRuns(activeConversationId);
        } else {
            console.log("[ChatClient] New conversation reset");
            if (isLoading) stop();
            latestRealtimeSeqRef.current = 0;
            realtimeMessageStateRef.current = createInitialSessionRealtimeMessageState<Message>([], WEB_STREAM_LIFECYCLE_OPTIONS);
            setScopeBinding(null);
            setSessionProjection(null);
            setLegacyChatUnsupported(false);
            clearApprovalState();
            setRunEntries([]);
            setQueuedMessages([]);
            setQueuedMessageMenuId(null);
            setEditingQueuedMessage(null);
            setQueuedMessageEditText("");
            setQueuedMessageError("");
            historyPagingModeRef.current = false;
            turnBeforeCursorRef.current = null;
            isLoadingOlderTurnsRef.current = false;
            setHasOlderTurns(false);
            setIsLoadingOlderTurns(false);
            messagesRef.current = [];
            setMessages([]);
        }
    }, [activeConversationId, clearApprovalState, isLoading, loadConversationHistory, loadRuns, loadSessionScope, stop, setMessages]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }

        const eventSource = new EventSource(`/api/realtime/sessions/${activeConversationId}/stream`);

        const handleSnapshot = (event: MessageEvent) => {
            try {
                const data = attachSseEventId(JSON.parse(event.data), event) as Record<string, unknown>;
                const snapshotPayload = (data?.payload && typeof data.payload === "object") ? data.payload : data;
                const snapshotRecord = snapshotPayload && typeof snapshotPayload === "object"
                    ? snapshotPayload as Record<string, unknown>
                    : {};
                const nestedSnapshot = snapshotRecord.snapshot && typeof snapshotRecord.snapshot === "object"
                    ? snapshotRecord.snapshot as Record<string, unknown>
                    : {};
                if (isLegacyChatUnsupportedPayload(snapshotPayload)) {
                    setLegacyChatUnsupported(true);
                }
                applyQueuedMessagesSnapshot(extractQueuedMessages(snapshotPayload));
                const localStreamActive = isLocalStreamActive(activeConversationId);
                const nextView = deriveAuthoritativeSessionView(snapshotPayload).view as SessionProjectionView | null;
                setSessionProjection((current) => {
                    if (!nextView) {
                        return current;
                    }
                    if (!current) {
                        return nextView;
                    }
                    return {
                        ...nextView,
                        contextGovernance: nextView.contextGovernance || current.contextGovernance,
                        contextGovernanceHistory:
                            Array.isArray(nextView.contextGovernanceHistory) && nextView.contextGovernanceHistory.length > 0
                                ? nextView.contextGovernanceHistory
                                : current.contextGovernanceHistory,
                    };
                });
                if (Array.isArray(nextView?.processes) && nextView.processes.length > 0) {
                    applySessionProcessSurface(nextView.processes);
                }
                if (!localStreamActive && !historyPagingModeRef.current && Array.isArray(nestedSnapshot.messages)) {
                    applyProjectedSnapshot(
                        nestedSnapshot.messages,
                        Number(snapshotRecord.latestSeq || nestedSnapshot.latest_seq || 0),
                    );
                }
            } catch (error) {
                console.warn("[ChatClient] Failed to parse snapshot SSE payload:", error);
            }
        };

        const handleRuntime = (event: MessageEvent) => {
            try {
                const rawEvent = attachSseEventId(JSON.parse(event.data), event);
                applyRemoteRuntimeEvent(rawEvent);
            } catch (error) {
                console.warn("[ChatClient] Failed to parse runtime SSE payload:", error);
            }
        };

        const handleError = () => {
            if (!isLocalStreamActive(activeConversationId)) {
                void loadConversationHistory(activeConversationId).catch((error) => {
                    console.warn("[ChatClient] Realtime resync failed:", error);
                });
            }
        };

        eventSource.addEventListener("snapshot", handleSnapshot as EventListener);
        eventSource.addEventListener("runtime", handleRuntime as EventListener);
        eventSource.addEventListener("error", handleError as EventListener);

        return () => {
            eventSource.removeEventListener("snapshot", handleSnapshot as EventListener);
            eventSource.removeEventListener("runtime", handleRuntime as EventListener);
            eventSource.removeEventListener("error", handleError as EventListener);
            eventSource.close();
        };
    }, [activeConversationId, applyProjectedSnapshot, applyQueuedMessagesSnapshot, applyRemoteRuntimeEvent, applySessionProcessSurface, isLocalStreamActive, loadConversationHistory]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }
        const hasRuntimeNeed = Boolean(
            currentRun?.id
            || sessionProjection?.runtimeStatus === "running"
            || sessionProjection?.controls?.canInterrupt,
        );
        if (hasRuntimeNeed && sessionProcessSurface.length > 0 && hudProcesses.length === 0) {
            console.warn("[ChatClient] process surface dropped after hydration/filtering", {
                activeConversationId,
                currentRunId: currentRun?.id || projectionRunId || null,
                sessionProcessSurface: sessionProcessSurface.length,
                projectionProcessSurface: (sessionProjection?.processes || []).length,
            });
        }
    }, [activeConversationId, currentRun?.id, hudProcesses.length, projectionRunId, sessionProcessSurface.length, sessionProjection?.controls?.canInterrupt, sessionProjection?.processes, sessionProjection?.runtimeStatus]);

    // Auth Check UI
    if (status === "loading") {
        return <div className="flex h-full items-center justify-center">{t("web.generated.3fe7e53e91")}</div>;
    }

    if (status === "unauthenticated") {
        return (
            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                    {localConnectError ? <AlertCircle className="h-7 w-7" /> : <PlugZap className="h-7 w-7" />}
                </div>
                <div className="mt-5 max-w-sm space-y-2">
                    <h1 className="text-xl font-semibold">
                        {localConnectError ? t("web.generated.a2c5172061") : t("web.generated.5d3147c6f4")}
                    </h1>
                    <p className="text-sm leading-6 text-muted-foreground">
                        {localConnectError || t("web.generated.36981fc5e7")}
                    </p>
                </div>
                {localConnectError && (
                    <Button className="mt-5 rounded-2xl" onClick={() => window.open(`${DEFAULT_LOCAL_ADMIN_BASE_URL}/admin`, "_blank", "noopener,noreferrer")}>
                        {t("web.generated.a5aa32a989")}
                    </Button>
                )}
            </div>
        );
    }

    const composerShellStyle = {
        paddingBottom: mobileKeyboardInset > 0
            ? `calc(0.5rem + env(safe-area-inset-bottom) + ${mobileKeyboardInset}px)`
            : "calc(0.5rem + env(safe-area-inset-bottom))",
    };
    const hasAskUserSurface = Boolean(askUserModalOpen && (askUserApprovalId || askUserToolCallId || askUserQuestion));

    return (
        <div className="relative flex h-full min-h-0 w-full overflow-hidden overscroll-none bg-transparent">
            {/* 中间+左侧 主工作区 */}
            <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
                {/* 消息与聊天流窗口 */}
                <div className={cn("mx-auto flex h-full min-h-0 w-full flex-1 flex-col px-2 pt-0.5 sm:px-4 sm:pt-1 lg:px-6", contentShellClassName)}>
                <div className="shrink-0 flex flex-col gap-1">
                    <div className="scrollbar-none flex flex-nowrap items-center gap-1 overflow-x-auto overflow-y-hidden pb-0.5 sm:gap-1">
                        {hasActiveWorkbenchSession && (
                            <div className="ml-auto flex shrink-0 justify-end gap-1">
                                {/* 终端开关 */}
                                <button
                                    type="button"
                                    className={cn(
                                        "inline-flex h-[28px] w-[28px] shrink-0 items-center justify-center rounded-xl border bg-background/78 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:text-foreground sm:h-[30px] sm:w-[30px]",
                                        terminalOpen ? "border-primary/35 bg-primary/8 text-primary" : "border-border/60",
                                    )}
                                    onClick={() => setTerminalOpen((prev) => !prev)}
                                    aria-label={t("web.generated.e7d892a681")}
                                    title={t("web.generated.e7d892a681")}
                                >
                                    <TerminalSquare className="h-[11px] w-[11px] shrink-0 sm:h-[13px] sm:w-[13px]" />
                                </button>

                                {/* 侧边栏开关 */}
                                <button
                                    type="button"
                                    className={cn(
                                        "inline-flex h-[28px] w-[28px] shrink-0 items-center justify-center rounded-xl border bg-background/78 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:text-foreground sm:h-[30px] sm:w-[30px]",
                                        workbenchMode !== "closed" ? "border-primary/35 bg-primary/8 text-primary" : "border-border/60",
                                    )}
                                    onClick={toggleWorkbench}
                                    aria-label={t("web.generated.3e0dd2a94a")}
                                    title={t("web.generated.3e0dd2a94a")}
                                >
                                    <PanelRight className="h-[11px] w-[11px] shrink-0 sm:h-[13px] sm:w-[13px]" />
                                </button>
                            </div>
                        )}
                    </div>

                </div>

                <div className="min-h-0 flex-1 overflow-hidden py-1 sm:py-1.5">
                    {messages.length === 0 && !activeConversationId ? (
                        <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-y-auto p-8 animate-in fade-in zoom-in-95 duration-500">
                            <div className="w-full max-w-3xl space-y-8">
                                <div className="space-y-6 text-center">
                                    <h1 className="bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-4xl font-bold tracking-tight text-transparent sm:text-5xl">
                                        {greetingText}
                                    </h1>
                                    <p className="text-sm text-muted-foreground">
                                        {t("web.generated.c8b86ec5eb")}
                                    </p>
                                </div>
                                {workspaceChooserVisible ? (
                                    <div className="rounded-[28px] border border-border/60 bg-background/88 p-5 shadow-lg backdrop-blur">
                                        <div className="flex items-center justify-between gap-3">
                                            <div>
                                                <h2 className="text-base font-semibold">{t("web.generated.d206213de7")}</h2>
                                                <p className="mt-1 text-xs text-muted-foreground">
                                                    {t("web.generated.81d22a4b01")}
                                                </p>
                                            </div>
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => {
                                                    setWorkspaceChooserVisible(false);
                                                    clearPendingContextSessionRefs();
                                                    clearNewConversationIntent();
                                                }}
                                            >
                                                {t("web.generated.4fe0ba039e")}
                                            </Button>
                                        </div>
                                        <div className="mt-5 grid gap-3">
                                            <button
                                                type="button"
                                                className="rounded-2xl border border-border/60 bg-muted/40 px-4 py-4 text-left transition hover:border-primary/35 hover:bg-primary/5"
                                                onClick={() => void createBoundConversation({ kind: "main" })}
                                                disabled={workspaceChooserBusy || !mainWorkspacePath}
                                            >
                                                <div className="text-sm font-semibold">{t("web.generated.8691094de0")}</div>
                                                <div className="mt-1 text-xs text-muted-foreground">{mainWorkspacePath || t("web.generated.7b7c3216d4")}</div>
                                            </button>
                                            <div className="rounded-2xl border border-border/60 bg-background/70 p-4">
                                                <div className="text-sm font-semibold">{t("web.generated.ce2328b304")}</div>
                                                <div className="mt-3 max-h-52 space-y-2 overflow-y-auto pr-1">
                                                    {projects.filter((project) => project.active !== false).length === 0 ? (
                                                        <div className="text-xs text-muted-foreground">
                                                            {t("web.generated.a3b9969c75")}
                                                        </div>
                                                    ) : (
                                                        projects
                                                            .filter((project) => project.active !== false)
                                                            .map((project) => (
                                                                <button
                                                                    key={project.id}
                                                                    type="button"
                                                                    className="w-full rounded-xl border border-border/60 bg-muted/30 px-3 py-3 text-left transition hover:border-primary/35 hover:bg-primary/5"
                                                                    onClick={() => void createBoundConversation({ kind: "project", projectId: project.id })}
                                                                    disabled={workspaceChooserBusy}
                                                                >
                                                                    <div className="text-sm font-medium">{project.name}</div>
                                                                    <div className="mt-1 text-xs text-muted-foreground">{project.workspacePath || project.id}</div>
                                                                </button>
                                                            ))
                                                    )}
                                                </div>
                                            </div>
                                            <div className="rounded-2xl border border-border/60 bg-background/70 p-4">
                                                <div className="text-sm font-semibold">{t("web.generated.79a9fe3510")}</div>
                                                <div className="mt-1 text-xs text-muted-foreground">
                                                    {t("web.generated.97f675327a")}
                                                </div>
                                                <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                                                    <input
                                                        value={newProjectPath}
                                                        onChange={(event) => setNewProjectPath(event.target.value)}
                                                        placeholder={t("web.generated.b47eb08b3a")}
                                                        className="h-11 flex-1 rounded-xl border border-border/60 bg-background px-3 text-sm outline-none transition focus:border-primary"
                                                    />
                                                    <Button
                                                        type="button"
                                                        className="h-11 rounded-xl"
                                                        onClick={() => void handleCreateProjectConversation()}
                                                        disabled={workspaceChooserBusy || newProjectPath.trim().length === 0}
                                                    >
                                                        {t("web.generated.36381d8034")}
                                                    </Button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex justify-center">
                                        <Button
                                            type="button"
                                            size="lg"
                                            className="rounded-2xl px-6"
                                            onClick={() => {
                                                setWorkspaceChooserVisible(true);
                                                clearNewConversationIntent();
                                            }}
                                        >
                                            {t("web.generated.30afc1958c")}
                                        </Button>
                                    </div>
                                )}
                            </div>
                        </div>
                    ) : activeConversationId && legacyChatUnsupported && messages.length === 0 ? (
                        <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-y-auto p-8 animate-in fade-in zoom-in-95 duration-300">
                            <div className="max-w-xl rounded-[28px] border border-amber-300/50 bg-amber-50/80 p-6 text-center shadow-sm backdrop-blur dark:border-amber-500/30 dark:bg-amber-500/10">
                                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200">
                                    <AlertCircle className="h-6 w-6" />
                                </div>
                                <h2 className="mt-4 text-base font-semibold text-foreground">
                                    {t("web.generated.c87db8211d")}
                                </h2>
                                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                                    {t("web.generated.b218378073")}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <ChatWindow
                            key={activeConversationId || "new"}
                            messages={messages}
                            processes={hudProcesses}
                            contextReferences={projectionContextReferences}
                            conversationId={activeConversationId}
                            isLoading={isLoading}
                            userAvatar={chatUserAvatar}
                            userName={chatUserName}
                            shellClassName="w-full"
                            runtimeActivities={runtimeStageModel.messageActivities}
                            sessionRunning={activeConversationRunning}
                            hasOlderTurns={hasOlderTurns}
                            isLoadingOlderTurns={isLoadingOlderTurns}
                            onReachTop={loadOlderConversationTurn}
                            onDeleteMessage={(messageId) => {
                                setMessages((prev) => prev.filter((message) => message.id !== messageId));
                                const conversationId = activeConversationIdRef.current;
                                if (conversationId) {
                                    void loadConversationHistory(conversationId).catch((error) => {
                                        console.warn("[ChatClient] Failed to refresh conversation after deleting message:", error);
                                    });
                                }
                            }}
                        />
                    )}
                </div>

                <div
                    className="shrink-0 pt-1 transition-[padding-bottom] duration-200 sm:pb-[calc(0.75rem+env(safe-area-inset-bottom))]"
                    style={composerShellStyle}
                >
                    <div className="flex flex-col gap-2">
                        <div className="relative shrink-0">
                            {activeConversationId && hasAskUserSurface ? (
                                <div className={cn("mb-2", askUserCollapsed && "hidden")}>
                                    <AskUserModal
                                        key={askUserApprovalId || askUserToolCallId || 'default-modal'}
                                        isOpen={askUserModalOpen && !askUserCollapsed}
                                        question={askUserQuestion}
                                        request={askUserRequest}
                                        toolCallId={askUserToolCallId}
                                        onSubmit={(_, answer, approve) => handleAskUserSubmit(answer, approve)}
                                        onCancel={() => setAskUserCollapsed(true)}
                                    />
                                </div>
                            ) : null}
                            {activeConversationId && hasAskUserSurface && askUserCollapsed ? (
                                <div className="mb-2 flex justify-end">
                                    <button
                                        type="button"
                                        className="rounded-full border border-border/55 bg-background/95 px-3 py-1.5 text-xs text-muted-foreground shadow-sm transition hover:border-primary/35 hover:text-foreground"
                                        onClick={() => setAskUserCollapsed(false)}
                                    >
                                        {t("web.generated.41b5da53c6")}
                                    </button>
                                </div>
                            ) : null}
                            {activeConversationId && visibleQueuedMessages.length > 0 ? (
                                <div className="mb-2">
                                    <QueuedMessagesStrip
                                        messages={visibleQueuedMessages}
                                        collapsed={queuedMessagesCollapsed}
                                        menuOpenId={queuedMessageMenuId}
                                        busyId={queuedMessageBusyId}
                                        labels={queueLabels}
                                        onToggleCollapsed={() => setQueuedMessagesCollapsed((current) => !current)}
                                        onOpenMenu={setQueuedMessageMenuId}
                                        onPromote={handlePromoteQueuedMessage}
                                        onCancel={handleCancelQueuedMessage}
                                        onEdit={handleOpenQueuedMessageEditor}
                                    />
                                    {queuedMessageError ? (
                                        <div className="mx-auto mt-1 max-w-4xl rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                                            {queuedMessageError}
                                        </div>
                                    ) : null}
                                </div>
                            ) : null}
                            {activeConversationId ? (
                                <InputArea
                                    key={activeConversationId || "new-session"}
                                    input={input}
                                    handleInputChange={handleInputChange}
                                    handleSubmit={handleSend}
                                    onVoiceTranscript={(transcript) => {
                                        setInput((prev) => {
                                            const prefix = prev.trim();
                                            return prefix ? `${prefix}\n${transcript}` : transcript;
                                        });
                                    }}
                                    onVoiceAudioMessage={handleVoiceAudioMessage}
                                    isLoading={isLoading}
                                    sessionRunning={activeConversationRunning}
                                    canStopRun={isLoading}
                                    onStop={stop}
                                    selectedAgentName={t("web.generated.675df2e7c7")}
                                    shellClassName="w-full"
                                    reasoningEffortControl={supervisorReasoningEffortControl}
                                    contextSessionRefs={pendingContextSessionRefs}
                                    contextUsagePercent={projectionContextUsagePercent}
                                    onRemoveContextSessionRef={(sessionId) => {
                                        setPendingContextSessionRefs((current) => {
                                            const next = current.filter((item) => item.sessionId !== sessionId);
                                            if (next.length === 0) {
                                                contextTakeoverConversationIdRef.current = null;
                                            }
                                            return next;
                                        });
                                    }}
                                />
                            ) : (
                                <div className="rounded-2xl border border-dashed border-border/60 bg-background/70 px-4 py-3 text-center text-sm text-muted-foreground">
                                    {t("web.generated.40e41202b3")}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* 底部折叠式终端栏面板 */}
            {hasActiveWorkbenchSession && terminalOpen && (
                <ManualTerminalPanel
                    workspacePath={terminalWorkspacePath}
                    profiles={terminalProfiles}
                    profileId={terminalProfileId}
                    sessions={visibleManualTerminalSessions}
                    processes={visibleTerminalProcesses}
                    activeTabId={activeTerminalTabId}
                    hiddenTabCount={hiddenTerminalTabCount}
                    busy={terminalBusy}
                    error={terminalError}
                    onProfileChange={setTerminalProfileId}
                    onStart={() => void startManualTerminal()}
                    onActivate={setActiveTerminalTabId}
                    onHideTab={hideTerminalTab}
                    onShowHidden={showHiddenTerminalTabs}
                    onClosePanel={() => setTerminalOpen(false)}
                />
            )}
        </div>

        {hasActiveWorkbenchSession ? (
            <WorkbenchShell
                sessionId={activeConversationId || ""}
                messages={messages}
                outputEvidence={governanceApprovals}
                processes={hudProcesses}
                todos={projectionTodos}
                todoStale={projectionTodoStale}
                runtimeModel={runtimeStageModel}
                workspacePath={scopeBinding?.workspacePath || mainWorkspacePath || ""}
                onSendFileLineComment={handleFileLineComment}
            />
        ) : null}

        <QueuedMessageEditDialog
            item={editingQueuedMessage}
            value={queuedMessageEditText}
            busy={queuedMessageEditBusy}
            labels={queueLabels}
            onChange={setQueuedMessageEditText}
            onCancel={() => {
                setEditingQueuedMessage(null);
                setQueuedMessageEditText("");
            }}
            onSave={handleSaveQueuedMessageEdit}
        />

        <GovernanceApprovalModal
            isOpen={governanceApprovalOpen}
            approval={governancePendingApproval}
            busy={governanceApprovalBusy}
            onApprove={(answer) => handleGovernanceApprovalResolve(answer, true)}
            onReject={(answer) => handleGovernanceApprovalResolve(answer, false)}
            onViewDetails={handleGovernanceApprovalViewDetails}
            onCancel={handleGovernanceApprovalDismiss}
        />

    </div>
    );
}
