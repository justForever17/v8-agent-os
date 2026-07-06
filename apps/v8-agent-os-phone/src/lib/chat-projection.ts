import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import type { AskUserInteraction, ChatMessage, ConversationSummary, PendingApproval, SessionTodoItem } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import {
    deriveMemoryRuntimeInsightFromGovernance,
    normalizeContextGovernanceDigest,
    normalizeContextGovernanceHistory,
    type AdminProcessRef,
    type ContextGovernanceView,
    type ContextReferenceItem,
} from "@v8/session-realtime";

import type { PhoneRuntimeId, PhoneRuntimeStageActivity, PhoneRuntimeStageCard, PhoneRuntimeTimelineEntry } from "@/src/lib/runtime-stage";
import { buildPhoneRuntimeStageModel } from "@/src/lib/runtime-stage";
type RuntimeSummary = {
    status: string;
    latestSeq: number;
    runId?: string;
    label?: string;
};

type Translate = (key: string, params?: Record<string, string | number>) => string;

const ACTIVE_PROCESS_STATUSES = new Set([
    "queued",
    "pending",
    "starting",
    "running",
    "streaming",
    "waiting_input",
    "waiting_approval",
]);

const TERMINAL_RUN_STATUSES = new Set([
    "completed",
    "failed",
    "cancelled",
    "paused",
    "idle",
]);

const STATUS_LABELS: Record<string, string> = {
    queued: "shared.runtime_status.queued",
    running: "shared.runtime_status.running",
    waiting_approval: "shared.runtime_status.waiting_approval",
    waiting_input: "shared.runtime_status.waiting_input",
    paused: "shared.runtime_status.paused",
    completed: "shared.runtime_status.completed",
    failed: "shared.runtime_status.failed",
    cancelled: "shared.runtime_status.cancelled",
    idle: "shared.runtime_status.idle",
};

export type PhoneChatProjection = {
    activeConversation: ConversationSummary | null;
    activeScopeTags: string[];
    projectedMessages: ChatMessage[];
    runtimeStageModel: ReturnType<typeof buildPhoneRuntimeStageModel>;
    selectedRuntimeId: PhoneRuntimeId | null;
    selectedRuntimeActivities: PhoneRuntimeStageActivity[];
    selectedRuntimeDockItem: PhoneRuntimeStageCard | undefined;
    currentRunLabel: string;
    currentStepTitle: string | null;
    historyPreview: string | null;
    pendingApproval: PendingApproval | AskUserInteraction | null;
    governancePendingApproval: PendingApproval | null;
    askUserPendingApproval: AskUserInteraction | null;
    pendingApprovalCount: number;
    todoCount: number;
    todos: SessionTodoItem[];
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    sidebarGroups: Record<"channels" | "cron" | "hooks" | "web", ConversationSummary[]>;
    runControlState: {
        runId?: string;
        status: string;
        pendingApproval: boolean;
        canOpenApproval?: boolean;
        canResume?: boolean;
        canRetry?: boolean;
        canInterrupt?: boolean;
    };
    voiceCardDescriptors: Array<{
        messageId: string;
        renderKey: string;
        autoPlayKey: string;
        voiceText: string;
    }>;
};

function deriveHistoryPreview(
    messages: ChatMessage[],
    activeConversation: ConversationSummary | null,
) {
    const projectedPreview = String(
        activeConversation?.previewExcerpt
        || activeConversation?.lastNarrativeExcerpt
        || "",
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

    return null;
}

function normalizeRunId(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function getMessageRunId(message: ChatMessage) {
    return normalizeRunId(message.runId);
}

function getTimelineEntryMessageId(entry: PhoneRuntimeTimelineEntry) {
    const metadata = entry.metadata && typeof entry.metadata === "object"
        ? entry.metadata as Record<string, unknown>
        : undefined;
    return String(
        metadata?.messageId
        || metadata?.message_id
        || metadata?.sourceMessageId
        || metadata?.source_message_id
        || "",
    ).trim() || undefined;
}

function getContextGovernanceRunId(view: ContextGovernanceView | null | undefined) {
    if (!view) {
        return undefined;
    }
    return normalizeRunId(view.runId || view.run_id);
}

function resolveLatestRuntimeRunId(
    messages: ChatMessage[],
    runtimeTimeline: PhoneRuntimeTimelineEntry[],
    preferredRunId?: string,
) {
    const normalizedPreferredRunId = normalizeRunId(preferredRunId);
    if (normalizedPreferredRunId) {
        const preferredExistsInMessages = messages.some((message) => getMessageRunId(message) === normalizedPreferredRunId);
        const preferredExistsInTimeline = runtimeTimeline.some((entry) => normalizeRunId(entry.runId) === normalizedPreferredRunId);
        if (preferredExistsInMessages || preferredExistsInTimeline) {
            return normalizedPreferredRunId;
        }
    }
    if (normalizedPreferredRunId && messages.length === 0 && runtimeTimeline.length === 0) {
        return normalizedPreferredRunId;
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const messageRunId = getMessageRunId(messages[index]);
        if (messageRunId) {
            return messageRunId;
        }
    }
    for (let index = runtimeTimeline.length - 1; index >= 0; index -= 1) {
        const entryRunId = normalizeRunId(runtimeTimeline[index]?.runId);
        if (entryRunId) {
            return entryRunId;
        }
    }
    return undefined;
}

function groupSidebarConversations(conversations: ConversationSummary[]) {
    return conversations.reduce<Record<"channels" | "cron" | "hooks" | "web", ConversationSummary[]>>(
        (groups, item) => {
            const key = item.sourceGroup === "cron"
                ? "cron"
                : item.sourceGroup === "hooks"
                    ? "hooks"
                    : item.sourceGroup === "channels"
                        ? "channels"
                        : "web";
            groups[key].push(item);
            return groups;
        },
        { channels: [], cron: [], hooks: [], web: [] },
    );
}

function collectVoiceCardDescriptors(messages: ChatMessage[]) {
    const descriptors: Array<{
        messageId: string;
        renderKey: string;
        autoPlayKey: string;
        voiceText: string;
    }> = [];

    messages.forEach((message) => {
        const renderKey = String(message.renderKey || message.id || "").trim();
        const messageIdentity = String(
            message.runId
            || renderKey
            || message.id
            || `${message.role}:${message.timestamp || 0}`,
        ).trim();

        if (Array.isArray(message.nodes) && message.nodes.length > 0) {
            message.nodes.forEach((node, nodeIndex) => {
                if (node.kind !== "narrative") {
                    return;
                }
                parsePhoneContentBlocks(String(node.content || ""))
                    .forEach((block, blockIndex) => {
                        if (block.type !== "voice" || !block.content.trim()) {
                            return;
                        }
                        descriptors.push({
                            messageId: String(message.id || "").trim(),
                            renderKey,
                            autoPlayKey: buildVoicePlaybackKey(
                                `${messageIdentity}:node:${nodeIndex}`,
                                String(blockIndex),
                                block.content,
                            ),
                            voiceText: block.content.trim(),
                        });
                    });
            });
            return;
        }

        parsePhoneContentBlocks(String(message.content || ""))
            .forEach((block, blockIndex) => {
                if (block.type !== "voice" || !block.content.trim()) {
                    return;
                }
                descriptors.push({
                    messageId: String(message.id || "").trim(),
                    renderKey,
                    autoPlayKey: buildVoicePlaybackKey(messageIdentity, String(blockIndex), block.content),
                    voiceText: block.content.trim(),
                });
            });
    });

    return descriptors.filter((item) => {
        if (!item.autoPlayKey || !item.voiceText) {
            return false;
        }
        if (!item.renderKey && !item.messageId) {
            return false;
        }
        return true;
    });
}

function normalizeRunStatus(value: unknown, fallback = "idle") {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized || fallback;
}

function isActiveProcess(process: AdminProcessRef, runId?: string) {
    const status = normalizeRunStatus(process.status, "");
    if (!status) {
        return false;
    }
    if (runId && process.runId && String(process.runId).trim() && String(process.runId).trim() !== runId) {
        return false;
    }
    if (ACTIVE_PROCESS_STATUSES.has(status)) {
        return true;
    }
    return !process.completedAt && !TERMINAL_RUN_STATUSES.has(status);
}

function matchesConversationProcess(
    process: AdminProcessRef,
    {
        conversationId,
        runId,
        messageIds,
    }: {
        conversationId?: string | null;
        runId?: string;
        messageIds: Set<string>;
    },
) {
    const processRecord = process as AdminProcessRef & {
        sessionId?: string | null;
    };
    const normalizedConversationId = String(conversationId || "").trim();
    if (!normalizedConversationId) {
        return true;
    }
    const processSessionId = String(processRecord.sessionId || "").trim();
    if (processSessionId) {
        return processSessionId === normalizedConversationId;
    }
    const processRunId = String(process.runId || "").trim();
    if (runId && processRunId && processRunId === runId) {
        return true;
    }
    const sourceMessageId = String(process.sourceMessageId || "").trim();
    if (sourceMessageId) {
        return messageIds.has(sourceMessageId);
    }
    // Session-level process routing is already authoritatively filtered by session.
    // If process metadata is incomplete, prefer keeping it over suppressing the HUD incorrectly.
    return true;
}

function deriveRunControlState({
    activeConversation,
    runtime,
    approvals,
    processes,
}: {
    activeConversation: ConversationSummary | null;
    runtime: RuntimeSummary;
    approvals: PendingApproval[];
    processes: AdminProcessRef[];
}) {
    const controls = activeConversation?.controls;
    const authoritativeStatus = normalizeRunStatus(
        activeConversation?.workflowStatus
        || activeConversation?.status
        || activeConversation?.workflowSummary?.workflowStatus
        || activeConversation?.workflowSummary?.stepStatus,
        "",
    );
    const optimisticStatus = normalizeRunStatus(runtime.status);
    const activeRunIdentity = String(
        activeConversation?.currentRunId
        || runtime.runId
        || "",
    ).trim() || undefined;
    const historicalRunIdentity = String(
        activeRunIdentity
        || activeConversation?.lastRunId
        || "",
    ).trim() || undefined;
    const hasPendingApproval = approvals.length > 0 || Boolean(activeConversation?.hasPendingApproval);
    const hasActiveProcess = processes.some((process) => isActiveProcess(process, activeRunIdentity));
    const canInterrupt = Boolean((controls?.canInterrupt && activeRunIdentity) || hasActiveProcess);
    const canRetry = Boolean(controls?.canRetry);
    const canResume = Boolean(controls?.canResume);
    const canOpenApproval = Boolean(hasPendingApproval || controls?.canOpenApproval);

    let status = optimisticStatus;
    if (hasPendingApproval) {
        status = "waiting_approval";
    } else if (authoritativeStatus === "waiting_input") {
        status = "waiting_input";
    } else if (!hasActiveProcess && canRetry && !ACTIVE_PROCESS_STATUSES.has(authoritativeStatus)) {
        status = "failed";
    } else if (!hasActiveProcess && canResume && !ACTIVE_PROCESS_STATUSES.has(authoritativeStatus)) {
        status = "paused";
    } else if (authoritativeStatus && TERMINAL_RUN_STATUSES.has(authoritativeStatus) && !hasActiveProcess) {
        status = authoritativeStatus;
    } else if (optimisticStatus === "running") {
        const authoritativeRunning = authoritativeStatus === "running";
        const hasCurrentRun = Boolean(activeConversation?.currentRunId);
        status = (authoritativeRunning && Boolean(activeRunIdentity)) || hasCurrentRun || canInterrupt || hasActiveProcess
            ? "running"
            : (authoritativeStatus || "idle");
    } else if (authoritativeStatus && optimisticStatus === "idle") {
        status = authoritativeStatus;
    }

    const shouldKeepRunId = Boolean(
        historicalRunIdentity
        && (
            status === "running"
            || status === "waiting_approval"
            || status === "waiting_input"
            || status === "failed"
            || status === "cancelled"
            || status === "paused"
            || canRetry
            || canResume
        ),
    );

    return {
        runId: shouldKeepRunId ? historicalRunIdentity : undefined,
        status,
        pendingApproval: hasPendingApproval,
        canOpenApproval,
        canResume,
        canRetry,
        canInterrupt,
    };
}

export function summarizePhoneRuntimeStatus(status: string, t: Translate) {
    const normalized = String(status || "idle").trim().toLowerCase();
    const label = STATUS_LABELS[normalized];
    return label ? t(label) : normalized || t("shared.runtime_status.idle");
}

export function summarizePhoneRuntimeTimelineEntry(entry: PhoneRuntimeTimelineEntry, t: Translate) {
    if (entry.topic === "ask_user.requested") {
        return t("src.lib.chat_projection.waiting_for_your_input");
    }
    if (entry.topic === "approval.requested") {
        return t("src.lib.chat_projection.waiting_for_approval");
    }
    return entry.summary || entry.topic || t("src.lib.chat_projection.runtime_updated");
}

export function buildPhoneChatProjection({
    conversations,
    activeConversationId,
    messages,
    approvals,
    askUserInteractions,
    todos,
    processes,
    contextReferences,
    contextGovernance,
    contextGovernanceHistory,
    runtime,
    runtimeTimeline,
    selectedRuntimeId,
    t,
    locale,
}: {
    conversations: ConversationSummary[];
    activeConversationId: string | null;
    messages: ChatMessage[];
    approvals: PendingApproval[];
    askUserInteractions?: AskUserInteraction[];
    todos: SessionTodoItem[];
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    contextGovernance?: ContextGovernanceView | null;
    contextGovernanceHistory?: ContextGovernanceView[];
    runtime: RuntimeSummary;
    runtimeTimeline: PhoneRuntimeTimelineEntry[];
    selectedRuntimeId: PhoneRuntimeId | null;
    t: Translate;
    locale: LocaleCode;
}): PhoneChatProjection {
    const activeConversation = conversations.find((item) => (item.sessionId || item.id) === activeConversationId) || null;
    const activeMessageIds = new Set(
        messages
            .map((message) => String(message.id || "").trim())
            .filter(Boolean),
    );
    const preferredActiveRunId = String(
        activeConversation?.currentRunId
        || activeConversation?.lastRunId
        || runtime.runId
        || "",
    ).trim() || undefined;
    const activeRunId = resolveLatestRuntimeRunId(messages, runtimeTimeline, preferredActiveRunId);
    const scopedProcesses = processes.filter((process) => matchesConversationProcess(process, {
        conversationId: activeConversationId,
        runId: activeRunId,
        messageIds: activeMessageIds,
    }));
    const historyPreview = deriveHistoryPreview(messages, activeConversation);
    const runtimeScopedMessages = activeRunId
        ? messages.filter((message) => getMessageRunId(message) === activeRunId)
        : messages;
    const runtimeScopedMessageIds = new Set(
        runtimeScopedMessages
            .map((message) => String(message.id || "").trim())
            .filter(Boolean),
    );
    const runtimeScopedTimeline = activeRunId
        ? runtimeTimeline.filter((entry) => {
            const entryRunId = normalizeRunId(entry.runId);
            if (entryRunId) {
                return entryRunId === activeRunId;
            }
            const messageId = getTimelineEntryMessageId(entry);
            return messageId ? runtimeScopedMessageIds.has(messageId) : false;
        })
        : runtimeTimeline;
    const scopedContextGovernance = (() => {
        if (!contextGovernance || !activeRunId) {
            return contextGovernance || null;
        }
        const governanceRunId = getContextGovernanceRunId(contextGovernance);
        if (governanceRunId && governanceRunId !== activeRunId) {
            return null;
        }
        return contextGovernance;
    })();
    const scopedContextGovernanceHistory = activeRunId
        ? (contextGovernanceHistory || []).filter((item) => {
            const governanceRunId = getContextGovernanceRunId(item);
            return !governanceRunId || governanceRunId === activeRunId;
        })
        : (contextGovernanceHistory || []);
    const memoryInsight = deriveMemoryRuntimeInsightFromGovernance(
        scopedContextGovernance,
        scopedContextGovernanceHistory,
    );
    const governanceDigest = normalizeContextGovernanceDigest(scopedContextGovernance);
    const governanceHistory = normalizeContextGovernanceHistory(scopedContextGovernanceHistory);
    const runtimeStageModel = buildPhoneRuntimeStageModel(runtimeScopedMessages, {
        ownerRuntime: activeConversation?.ownerRuntime || null,
        status: runtime.status,
        pendingApproval: approvals.length > 0,
        currentStepTitle: activeConversation?.currentStepTitle || activeConversation?.workflowStatus || null,
        runtimeTimeline: runtimeScopedTimeline,
        memoryInsight,
        governanceDigest,
        governanceHistory,
        locale,
    });

    const runtimeIdsWithActivities = new Set(
        runtimeStageModel.activities
            .filter((entry) => !entry.synthetic)
            .map((entry) => entry.runtimeId),
    );
    const preferredRuntimeId = runtimeStageModel.items.find((item) => runtimeIdsWithActivities.has(item.id))?.id
        || runtimeStageModel.activeRuntimeId
        || runtimeStageModel.items[0]?.id
        || null;
    const resolvedRuntimeId = selectedRuntimeId && runtimeStageModel.items.some((item) => item.id === selectedRuntimeId)
        ? selectedRuntimeId
        : preferredRuntimeId;
    const governanceApprovals = approvals;
    const askUserPendingApproval = (askUserInteractions || []).find((item) => String(item.status || "pending").toLowerCase() === "pending") || null;
    const governancePendingApproval = approvals[0] || null;
    const preferredPendingApproval = askUserPendingApproval || governancePendingApproval;
    const runControlState = deriveRunControlState({
        activeConversation,
        runtime,
        approvals,
        processes: scopedProcesses,
    });

    return {
        activeConversation,
        activeScopeTags: Array.isArray(activeConversation?.scopeTags)
            ? activeConversation.scopeTags.filter(Boolean).slice(0, 3)
            : [],
        projectedMessages: messages,
        runtimeStageModel,
        selectedRuntimeId: resolvedRuntimeId,
        selectedRuntimeActivities: resolvedRuntimeId
            ? runtimeStageModel.activities.filter((entry) => entry.runtimeId === resolvedRuntimeId)
            : [],
        selectedRuntimeDockItem: runtimeStageModel.items.find((item) => item.id === resolvedRuntimeId) || runtimeStageModel.items[0],
        currentRunLabel: summarizePhoneRuntimeStatus(runtime.status, t),
        currentStepTitle: activeConversation?.currentStepTitle || activeConversation?.workflowStatus || null,
        historyPreview,
        pendingApproval: preferredPendingApproval,
        governancePendingApproval,
        askUserPendingApproval,
        pendingApprovalCount: governanceApprovals.length,
        todoCount: todos.length,
        todos,
        processes: scopedProcesses,
        contextReferences,
        sidebarGroups: groupSidebarConversations(conversations),
        runControlState,
        voiceCardDescriptors: collectVoiceCardDescriptors(messages),
    };
}
