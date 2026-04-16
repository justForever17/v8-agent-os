import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import type { ChatMessage, ConversationSummary, PendingApproval, SessionTodoItem } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import {
    deriveMemoryRuntimeInsightFromGovernance,
    isAskUserInteractionApproval,
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

type Translate = (zh: string, en?: string) => string;

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

const STATUS_LABELS: Record<string, { zh: string; en: string }> = {
    queued: { zh: "排队中", en: "Queued" },
    running: { zh: "执行中", en: "Running" },
    waiting_approval: { zh: "等待审批", en: "Waiting" },
    waiting_input: { zh: "等待输入", en: "Input" },
    paused: { zh: "已暂停", en: "Paused" },
    completed: { zh: "已完成", en: "Done" },
    failed: { zh: "失败", en: "Failed" },
    cancelled: { zh: "已取消", en: "Cancelled" },
    idle: { zh: "空闲", en: "Idle" },
};

export type PhoneChatProjection = {
    activeConversation: ConversationSummary | null;
    activeScopeTags: string[];
    projectedMessages: ChatMessage[];
    runtimeStageModel: ReturnType<typeof buildPhoneRuntimeStageModel>;
    selectedRuntimeId: PhoneRuntimeId;
    selectedRuntimeActivities: PhoneRuntimeStageActivity[];
    selectedRuntimeDockItem: PhoneRuntimeStageCard | undefined;
    currentRunLabel: string;
    currentStepTitle: string | null;
    historyPreview: string | null;
    pendingApproval: PendingApproval | null;
    governancePendingApproval: PendingApproval | null;
    askUserPendingApproval: PendingApproval | null;
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
    // 会话级 process route 已经按 session 做过 authoritative 过滤；
    // 如果 process 元数据不完整，这里宁可保留也不要把 HUD 再次误杀。
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
    const governanceApprovals = approvals.filter((item) => !isAskUserInteractionApproval(item));
    const controls = activeConversation?.controls;
    const authoritativeStatus = normalizeRunStatus(
        activeConversation?.workflowStatus
        || activeConversation?.status
        || activeConversation?.workflowSummary?.workflowStatus
        || activeConversation?.workflowSummary?.stepStatus,
        "",
    );
    const optimisticStatus = normalizeRunStatus(runtime.status);
    const runIdentity = String(
        activeConversation?.currentRunId
        || activeConversation?.lastRunId
        || runtime.runId
        || "",
    ).trim() || undefined;
    const hasPendingApproval = governanceApprovals.length > 0 || Boolean(activeConversation?.hasPendingApproval);
    const hasActiveProcess = processes.some((process) => isActiveProcess(process, runIdentity));
    const canInterrupt = Boolean(controls?.canInterrupt || hasActiveProcess);
    const canRetry = Boolean(controls?.canRetry);
    const canResume = Boolean(controls?.canResume);
    const canOpenApproval = Boolean(hasPendingApproval || controls?.canOpenApproval);

    let status = optimisticStatus;
    if (hasPendingApproval) {
        status = "waiting_approval";
    } else if (authoritativeStatus === "waiting_input") {
        status = "waiting_input";
    } else if (authoritativeStatus && TERMINAL_RUN_STATUSES.has(authoritativeStatus) && !hasActiveProcess) {
        status = authoritativeStatus;
    } else if (optimisticStatus === "running") {
        const authoritativeRunning = authoritativeStatus === "running";
        const hasCurrentRun = Boolean(activeConversation?.currentRunId);
        status = authoritativeRunning || hasCurrentRun || canInterrupt || hasActiveProcess
            ? "running"
            : (authoritativeStatus || "idle");
    } else if (authoritativeStatus && optimisticStatus === "idle") {
        status = authoritativeStatus;
    }

    const shouldKeepRunId = Boolean(
        runIdentity
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
        runId: shouldKeepRunId ? runIdentity : undefined,
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
    return label ? t(label.zh, label.en) : normalized || t("空闲", "Idle");
}

export function summarizePhoneRuntimeTimelineEntry(entry: PhoneRuntimeTimelineEntry, t: Translate) {
    if (entry.topic === "ask_user.requested") {
        return t("等待你的输入", "Waiting for your input");
    }
    if (entry.topic === "approval.requested") {
        return t("等待用户确认", "Waiting for approval");
    }
    return entry.summary || entry.topic || t("运行已更新", "Runtime updated");
}

export function buildPhoneChatProjection({
    conversations,
    activeConversationId,
    messages,
    approvals,
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
    todos: SessionTodoItem[];
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    contextGovernance?: ContextGovernanceView | null;
    contextGovernanceHistory?: ContextGovernanceView[];
    runtime: RuntimeSummary;
    runtimeTimeline: PhoneRuntimeTimelineEntry[];
    selectedRuntimeId: PhoneRuntimeId;
    t: Translate;
    locale: LocaleCode;
}): PhoneChatProjection {
    const activeConversation = conversations.find((item) => (item.sessionId || item.id) === activeConversationId) || null;
    const activeMessageIds = new Set(
        messages
            .map((message) => String(message.id || "").trim())
            .filter(Boolean),
    );
    const activeRunId = String(
        activeConversation?.currentRunId
        || activeConversation?.lastRunId
        || runtime.runId
        || "",
    ).trim() || undefined;
    const scopedProcesses = processes.filter((process) => matchesConversationProcess(process, {
        conversationId: activeConversationId,
        runId: activeRunId,
        messageIds: activeMessageIds,
    }));
    const historyPreview = deriveHistoryPreview(messages, activeConversation);
    const memoryInsight = deriveMemoryRuntimeInsightFromGovernance(
        contextGovernance || null,
        contextGovernanceHistory || [],
    );
    const governanceDigest = normalizeContextGovernanceDigest(contextGovernance || null);
    const governanceHistory = normalizeContextGovernanceHistory(contextGovernanceHistory || []);
    const runtimeStageModel = buildPhoneRuntimeStageModel(messages, {
        ownerRuntime: activeConversation?.ownerRuntime || null,
        status: runtime.status,
        pendingApproval: approvals.filter((item) => !isAskUserInteractionApproval(item)).length > 0,
        currentStepTitle: activeConversation?.currentStepTitle || activeConversation?.workflowStatus || null,
        runtimeTimeline,
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
        || "chat";
    const resolvedRuntimeId = runtimeStageModel.items.some((item) => item.id === selectedRuntimeId)
        ? selectedRuntimeId
        : preferredRuntimeId;
    const governanceApprovals = approvals.filter((item) => !isAskUserInteractionApproval(item));
    const askUserPendingApproval = approvals.find((item) => isAskUserInteractionApproval(item)) || null;
    const governancePendingApproval = governanceApprovals[0] || null;
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
        selectedRuntimeActivities: runtimeStageModel.activities.filter((entry) => entry.runtimeId === resolvedRuntimeId),
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
