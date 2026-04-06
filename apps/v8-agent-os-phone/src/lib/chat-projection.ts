import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import type { ArtifactDetail, ChatMessage, ConversationSummary, PendingApproval, SessionTodoItem } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import type { AdminProcessRef, ContextReferenceItem } from "@v8/session-realtime";

import type { PhoneRuntimeId, PhoneRuntimeStageActivity, PhoneRuntimeStageCard, PhoneRuntimeTimelineEntry } from "@/src/lib/runtime-stage";
import { buildPhoneRuntimeStageModel } from "@/src/lib/runtime-stage";
type RuntimeSummary = {
    status: string;
    latestSeq: number;
    runId?: string;
    label?: string;
};

type Translate = (zh: string, en?: string) => string;

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
    pendingApprovalCount: number;
    todoCount: number;
    artifactCount: number;
    artifacts: ArtifactDetail[];
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

export function summarizePhoneRuntimeStatus(status: string, t: Translate) {
    const normalized = String(status || "idle").trim().toLowerCase();
    const label = STATUS_LABELS[normalized];
    return label ? t(label.zh, label.en) : normalized || t("空闲", "Idle");
}

export function summarizePhoneRuntimeTimelineEntry(entry: PhoneRuntimeTimelineEntry, t: Translate) {
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
    artifacts,
    processes,
    contextReferences,
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
    artifacts: ArtifactDetail[];
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    runtime: RuntimeSummary;
    runtimeTimeline: PhoneRuntimeTimelineEntry[];
    selectedRuntimeId: PhoneRuntimeId;
    t: Translate;
    locale: LocaleCode;
}): PhoneChatProjection {
    const activeConversation = conversations.find((item) => item.id === activeConversationId) || null;
    const historyPreview = deriveHistoryPreview(messages, activeConversation);
    const runtimeStageModel = buildPhoneRuntimeStageModel(messages, {
        ownerRuntime: activeConversation?.ownerRuntime || null,
        status: runtime.status,
        pendingApproval: approvals.length > 0,
        currentStepTitle: activeConversation?.currentStepTitle || activeConversation?.workflowStatus || null,
        runtimeTimeline,
        locale,
    });

    const runtimeIdsWithActivities = new Set(runtimeStageModel.activities.map((entry) => entry.runtimeId));
    const preferredRuntimeId = runtimeStageModel.items.find((item) => runtimeIdsWithActivities.has(item.id))?.id
        || runtimeStageModel.activeRuntimeId
        || runtimeStageModel.items[0]?.id
        || "chat";
    const resolvedRuntimeId = runtimeStageModel.items.some((item) => item.id === selectedRuntimeId)
        ? selectedRuntimeId
        : preferredRuntimeId;

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
        pendingApproval: approvals[0] || null,
        pendingApprovalCount: approvals.length,
        todoCount: todos.length,
        artifactCount: artifacts.length,
        artifacts,
        todos,
        processes,
        contextReferences,
        sidebarGroups: groupSidebarConversations(conversations),
        runControlState: {
            runId: runtime.runId,
            status: runtime.status,
            pendingApproval: approvals.length > 0,
            canOpenApproval: approvals.length > 0 || activeConversation?.controls?.canOpenApproval,
            canResume: activeConversation?.controls?.canResume,
            canRetry: activeConversation?.controls?.canRetry,
            canInterrupt: activeConversation?.controls?.canInterrupt,
        },
        voiceCardDescriptors: collectVoiceCardDescriptors(messages),
    };
}
