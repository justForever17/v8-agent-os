import type {
    ChatArtifact,
    ChatMessage,
    PhoneUiArtifactNode,
    PhoneUiExecutionNode,
    PhoneUiGovernanceNode,
    PhoneUiTimelineNode,
} from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import {
    buildAuthoritativeRuntimeTimelineEntryFromEvent,
    coerceAdminResourceRef,
    type MemoryRuntimeInsight,
    getRuntimeRegistryEntry,
    normalizeAuthoritativeRuntimeTimeline,
    normalizeSessionRuntimeEvent,
    normalizeRuntimeId as normalizeSharedRuntimeId,
    SESSION_RUNTIME_ORDER,
    VISIBLE_SESSION_RUNTIME_ORDER,
    type AuthoritativeRuntimeTimelineEntry,
    type SessionRuntimeId,
} from "@v8/session-realtime";

export type PhoneRuntimeId =
    SessionRuntimeId;

export type PhoneRuntimeCardStatus = "idle" | "recent" | "active" | "attention";

export type PhoneRuntimeTimelineEntry = AuthoritativeRuntimeTimelineEntry;

export type PhoneRuntimeStageCard = {
    id: PhoneRuntimeId;
    label: string;
    shortLabel: string;
    description: string;
    status: PhoneRuntimeCardStatus;
    eventCount: number;
    lastActivity?: string;
    lastTimestamp?: number;
    stepTitle?: string;
    pendingApproval?: boolean;
};

export type PhoneRuntimeStageActivity = {
    id: string;
    runtimeId: PhoneRuntimeId;
    timestamp: number;
    summary: string;
    topic?: string;
    actorLabel?: string;
    messageId: string;
    node: PhoneUiTimelineNode;
    kind: "progress" | "tool" | "governance" | "artifact" | "handoff";
    synthetic?: boolean;
};

export type PhoneRuntimeStageModel = {
    activeRuntimeId: PhoneRuntimeId | null;
    items: PhoneRuntimeStageCard[];
    activities: PhoneRuntimeStageActivity[];
};

type RuntimeDescriptor = {
    id: PhoneRuntimeId;
    label: { zh: string; en: string };
    shortLabel: { zh: string; en: string };
    description: { zh: string; en: string };
};

function isEnglishLocale(locale: LocaleCode = "zh-CN") {
    return locale === "en";
}

function rt(locale: LocaleCode, zh: string, en: string) {
    return isEnglishLocale(locale) ? en : zh;
}

const RUNTIME_DESCRIPTORS: Record<PhoneRuntimeId, RuntimeDescriptor> = {
} as Record<PhoneRuntimeId, RuntimeDescriptor>;

for (const runtimeId of SESSION_RUNTIME_ORDER) {
    const zhDescriptor = getRuntimeRegistryEntry(runtimeId, "zh-CN");
    const enDescriptor = getRuntimeRegistryEntry(runtimeId, "en");
    RUNTIME_DESCRIPTORS[runtimeId] = {
        id: runtimeId,
        label: { zh: zhDescriptor.label, en: enDescriptor.label },
        shortLabel: { zh: zhDescriptor.shortLabel, en: enDescriptor.shortLabel },
        description: { zh: zhDescriptor.description, en: enDescriptor.description },
    };
}

export const PHONE_RUNTIME_ORDER: PhoneRuntimeId[] = [
    ...SESSION_RUNTIME_ORDER,
];

export const VISIBLE_PHONE_RUNTIME_ORDER: PhoneRuntimeId[] = [...VISIBLE_SESSION_RUNTIME_ORDER];

function firstRuntimeMatch(values: Array<string | null | undefined>): PhoneRuntimeId | null {
    for (const value of values) {
        const match = normalizePhoneRuntimeId(value);
        if (match) {
            return match;
        }
    }
    return null;
}

function inferRuntimeIdFromArtifactNode(node: PhoneUiArtifactNode): PhoneRuntimeId | null {
    const artifact = node.artifact as ChatArtifact & { metadata?: Record<string, unknown> };
    const metadata = artifact.metadata && typeof artifact.metadata === "object" ? artifact.metadata : undefined;
    return firstRuntimeMatch([
        typeof metadata?.runtime === "string" ? metadata.runtime : undefined,
        typeof metadata?.runtimeId === "string" ? metadata.runtimeId : undefined,
        artifact.sourcePath,
        artifact.workspacePath,
        artifact.previewUrl,
        artifact.id,
    ]);
}

export function inferPhoneRuntimeIdFromNode(node: PhoneUiTimelineNode): PhoneRuntimeId | null {
    if (node.kind === "artifact") {
        return inferRuntimeIdFromArtifactNode(node);
    }

    if (node.kind === "execution") {
        const executionNode = node as PhoneUiExecutionNode;
        return firstRuntimeMatch([
            typeof executionNode.data?.runtime === "string" ? executionNode.data.runtime : undefined,
            typeof executionNode.data?.runtimeId === "string" ? executionNode.data.runtimeId : undefined,
            executionNode.topic,
            executionNode.toolName,
            executionNode.agentName,
            executionNode.agentRoleLabel,
            executionNode.label,
        ]);
    }

    if (node.kind === "governance") {
        const governanceNode = node as PhoneUiGovernanceNode;
        return firstRuntimeMatch([
            governanceNode.topic,
            governanceNode.reason,
            governanceNode.agentName,
            governanceNode.agentRoleLabel,
        ]);
    }

    return firstRuntimeMatch([node.agentName, node.agentRoleLabel]);
}

function summarizeExecutionNode(node: PhoneUiExecutionNode): string | null {
    if (node.executionType === "runtime_progress") {
        return node.label || node.topic || "运行中";
    }
    if (node.executionType === "tool_call") {
        return node.toolName ? `调用 ${node.toolName}` : "工具调用";
    }
    if (node.executionType === "tool_result") {
        return node.toolCallId ? `工具结果 ${node.toolCallId}` : "工具结果";
    }
    if (node.executionType === "agent_start") {
        return node.agentName ? `${node.agentName} 已接入` : "协作单元已接入";
    }
    if (node.executionType === "reasoning") {
        return "正在推理";
    }
    return null;
}

function summarizeGovernanceNode(node: PhoneUiGovernanceNode): string | null {
    if (node.governanceType === "ask_user") {
        return node.question || "等待你的输入";
    }
    if (node.governanceType === "approval_request") {
        return node.question || "等待授权确认";
    }
    if (node.governanceType === "approval_resolved") {
        return node.reason || node.status || "审批状态已更新";
    }
    if (node.governanceType === "safety_blocked") {
        return node.reason || "安全预检已阻断当前运行";
    }
    if (node.governanceType === "context_governance") {
        return node.reason || "上下文治理已更新";
    }
    if (node.governanceType === "lane_updated") {
        return node.reason || "运行调度状态已更新";
    }
    return node.reason || node.status || node.topic || "运行控制已更新";
}

function summarizeArtifactNode(node: PhoneUiArtifactNode): string | null {
    return node.artifact.displayLabel || node.artifact.title || node.artifact.id || "新的产物";
}

function summarizeTimelineNode(node: PhoneUiTimelineNode): { summary: string; kind: PhoneRuntimeStageActivity["kind"] } | null {
    if (node.kind === "execution") {
        const summary = summarizeExecutionNode(node);
        if (!summary) return null;
        return {
            summary,
            kind: node.executionType === "tool_call" || node.executionType === "tool_result"
                ? "tool"
                : node.executionType === "agent_start"
                    ? "handoff"
                    : "progress",
        };
    }

    if (node.kind === "governance") {
        const summary = summarizeGovernanceNode(node);
        if (!summary) return null;
        return { summary, kind: "governance" };
    }

    if (node.kind === "artifact") {
        const summary = summarizeArtifactNode(node);
        if (!summary) return null;
        return { summary, kind: "artifact" };
    }

    return null;
}

function getNodeTimestamp(message: ChatMessage, node: PhoneUiTimelineNode): number {
    if (typeof node.timestamp === "number" && Number.isFinite(node.timestamp)) {
        return node.timestamp;
    }
    return Number(message.timestamp || Date.now());
}

function coerceTimelineString(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function buildTimelineArtifactNode(entry: PhoneRuntimeTimelineEntry): PhoneUiArtifactNode {
    const metadata = entry.metadata && typeof entry.metadata === "object"
        ? entry.metadata as Record<string, unknown>
        : {};
    const artifactId = coerceTimelineString(metadata.artifactId)
        || coerceTimelineString(metadata.id)
        || coerceTimelineString(metadata.messageId)
        || entry.id;

    return {
        id: `timeline-node-${entry.id}`,
        kind: "artifact",
        timestamp: entry.timestamp,
        agentName: entry.actorLabel,
        agentRoleLabel: entry.actorLabel,
        artifact: {
            id: artifactId,
            artifactId: coerceTimelineString(metadata.artifactId),
            title: coerceTimelineString(metadata.title) || entry.summary,
            displayLabel: coerceTimelineString(metadata.displayLabel) || coerceTimelineString(metadata.title) || entry.summary,
            displaySubtitle: coerceTimelineString(metadata.displaySubtitle)
                || coerceTimelineString(metadata.workspacePath)
                || coerceTimelineString(metadata.sourcePath)
                || coerceTimelineString(metadata.kind),
            kind: coerceTimelineString(metadata.kind),
            mimeType: coerceTimelineString(metadata.mimeType),
            previewUrl: coerceTimelineString(metadata.previewUrl),
            externalUrl: coerceTimelineString(metadata.externalUrl),
            sourcePath: coerceTimelineString(metadata.sourcePath),
            workspacePath: coerceTimelineString(metadata.workspacePath),
            resourceRef: coerceAdminResourceRef(metadata.resourceRef || metadata.previewUrl || metadata.externalUrl || metadata.sourcePath || metadata.workspacePath),
            runId: entry.runId,
            metadata,
        },
    };
}

function buildNodeFromTimelineEntry(entry: PhoneRuntimeTimelineEntry): PhoneUiTimelineNode {
    const governanceType = (() => {
        const topic = String(entry.topic || "").trim().toLowerCase();
        const metadata = entry.metadata && typeof entry.metadata === "object"
            ? entry.metadata as Record<string, unknown>
            : {};
        const approvalKind = String(metadata.approvalKind || metadata.approval_kind || "").trim().toLowerCase();
        const interactionKind = String(metadata.interactionKind || metadata.interaction_kind || "").trim().toLowerCase();
        if (topic === "approval.requested") {
            if (
                interactionKind === "ask_user"
                || approvalKind === "ask_user"
            ) {
                return "ask_user" as const;
            }
            return "approval_request" as const;
        }
        if (topic.startsWith("approval.")) return "approval_resolved" as const;
        if (topic.startsWith("safety.")) return "safety_blocked" as const;
        if (topic.startsWith("context.")) return "context_governance" as const;
        if (topic.startsWith("run.lane.")) return "lane_updated" as const;
        return "run_controlled" as const;
    })();
    if (entry.kind === "governance") {
        return {
            id: `timeline-node-${entry.id}`,
            kind: "governance",
            governanceType,
            topic: entry.topic,
            status: entry.status,
            reason: entry.summary,
            timestamp: entry.timestamp,
            agentName: entry.actorLabel,
            agentRoleLabel: entry.actorLabel,
        };
    }

    if (entry.kind === "artifact") {
        return buildTimelineArtifactNode(entry);
    }

    const metadata = entry.metadata && typeof entry.metadata === "object"
        ? entry.metadata as Record<string, unknown>
        : undefined;
    const content = coerceTimelineString(
        metadata?.content
        || metadata?.summary
        || metadata?.message
        || metadata?.reason
        || metadata?.label,
    );
    const executionType = entry.kind === "tool"
        ? "tool_call"
        : entry.kind === "handoff"
            ? "agent_start"
            : "runtime_progress";

    return {
        id: `timeline-node-${entry.id}`,
        kind: "execution",
        executionType,
        topic: entry.topic,
        label: entry.summary,
        content,
        toolName: executionType === "tool_call" ? coerceTimelineString(metadata?.toolName || metadata?.tool_name) : undefined,
        toolCallId: executionType === "tool_call" ? coerceTimelineString(metadata?.toolCallId || metadata?.tool_call_id || metadata?.approval_id) : undefined,
        args: executionType === "tool_call" ? metadata?.args ?? metadata?.request : undefined,
        result: executionType !== "tool_call" ? metadata?.result ?? metadata?.response ?? metadata?.result_preview : undefined,
        data: entry.metadata,
        timestamp: entry.timestamp,
        agentName: entry.actorLabel,
        agentRoleLabel: entry.actorLabel,
    };
}

export function formatPhoneRelativeRuntimeTime(
    timestamp?: number,
    locale: LocaleCode = "zh-CN",
): string {
    if (!timestamp) return rt(locale, "刚刚", "Just now");
    const diffMs = Date.now() - timestamp;
    const diffMinutes = Math.max(0, Math.floor(diffMs / 60000));
    if (diffMinutes < 1) return rt(locale, "刚刚", "Just now");
    if (diffMinutes < 60) return isEnglishLocale(locale) ? `${diffMinutes}m ago` : `${diffMinutes} 分钟前`;
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return isEnglishLocale(locale) ? `${diffHours}h ago` : `${diffHours} 小时前`;
    const diffDays = Math.floor(diffHours / 24);
    return isEnglishLocale(locale) ? `${diffDays}d ago` : `${diffDays} 天前`;
}

export function normalizePhoneRuntimeId(raw?: string | null): PhoneRuntimeId | null {
    return normalizeSharedRuntimeId(raw);
}

export function mergePhoneRuntimeTimeline(
    current: PhoneRuntimeTimelineEntry[],
    incoming: PhoneRuntimeTimelineEntry[],
) {
    const map = new Map<string, PhoneRuntimeTimelineEntry>();

    for (const item of [...current, ...incoming]) {
        const key = item.id || `${item.runtimeId}:${item.topic}:${item.timestamp}`;
        const existing = map.get(key);
        if (!existing || item.timestamp >= existing.timestamp) {
            map.set(key, item);
        }
    }

    return Array.from(map.values()).sort((left, right) => right.timestamp - left.timestamp);
}

export function getPhoneRuntimeDescriptor(runtimeId: PhoneRuntimeId, locale: LocaleCode = "zh-CN") {
    const descriptor = RUNTIME_DESCRIPTORS[runtimeId];
    return {
        id: descriptor.id,
        label: locale === "en" ? descriptor.label.en : descriptor.label.zh,
        shortLabel: locale === "en" ? descriptor.shortLabel.en : descriptor.shortLabel.zh,
        description: locale === "en" ? descriptor.description.en : descriptor.description.zh,
    };
}

export function normalizePhoneRuntimeTimeline(input: unknown[]): PhoneRuntimeTimelineEntry[] {
    return normalizeAuthoritativeRuntimeTimeline(input);
}

export function buildPhoneRuntimeTimelineEntryFromEvent(
    raw: unknown,
    localeOrOptions: LocaleCode | { locale?: LocaleCode } = "zh-CN",
): PhoneRuntimeTimelineEntry | null {
    const locale = typeof localeOrOptions === "string" ? localeOrOptions : (localeOrOptions.locale || "zh-CN");
    return buildAuthoritativeRuntimeTimelineEntryFromEvent(raw, { locale });
}

export function buildPhoneRuntimeStageModel(
    messages: ChatMessage[],
    options?: {
        ownerRuntime?: string | null;
        status?: string | null;
        pendingApproval?: boolean;
        currentStepTitle?: string | null;
        runtimeTimeline?: PhoneRuntimeTimelineEntry[] | null;
        memoryInsight?: MemoryRuntimeInsight | null;
        locale?: LocaleCode;
    },
): PhoneRuntimeStageModel {
    const activities: PhoneRuntimeStageActivity[] = [];

    for (const message of messages) {
        for (const node of Array.isArray(message.nodes) ? message.nodes : []) {
            const runtimeId = inferPhoneRuntimeIdFromNode(node);
            const summarized = summarizeTimelineNode(node);
            if (!runtimeId || !summarized) continue;

            activities.push({
                id: node.id,
                runtimeId,
                timestamp: getNodeTimestamp(message, node),
                summary: summarized.summary,
                topic: node.kind === "execution" ? node.topic : node.kind === "governance" ? node.topic : undefined,
                actorLabel: node.agentName || node.agentRoleLabel,
                messageId: message.id,
                node,
                kind: summarized.kind,
            });
        }
    }

    const seenTimelineKeys = new Set<string>();
    for (const entry of options?.runtimeTimeline || []) {
        const key = `${entry.id}:${entry.seq || 0}`;
        if (seenTimelineKeys.has(key)) {
            continue;
        }
        seenTimelineKeys.add(key);
        activities.push({
            id: entry.id,
            runtimeId: entry.runtimeId,
            timestamp: entry.timestamp,
            summary: entry.summary,
            topic: entry.topic,
            actorLabel: entry.actorLabel,
            messageId: entry.runId || entry.id,
            node: buildNodeFromTimelineEntry(entry),
            kind: entry.kind || "progress",
        });
    }

    if (options?.memoryInsight) {
        const topScoresLabel = options.memoryInsight.topScores.length > 0
            ? `Top Scores: ${options.memoryInsight.topScores.slice(0, 3).map((score) => score.toFixed(2)).join(", ")}`
            : null;
        const detailParts = [
            options.memoryInsight.query ? `Query: ${options.memoryInsight.query}` : null,
            options.memoryInsight.rejectReason ? `Reject: ${options.memoryInsight.rejectReason}` : null,
            topScoresLabel,
        ].filter((item): item is string => Boolean(item));
        activities.push({
            id: options.memoryInsight.id,
            runtimeId: "memory",
            timestamp: options.memoryInsight.timestamp,
            summary: options.memoryInsight.summary,
            topic: "memory.recall.insight",
            actorLabel: "记忆召回",
            messageId: options.memoryInsight.id,
            node: {
                id: `timeline-node-${options.memoryInsight.id}`,
                kind: "execution",
                executionType: "runtime_progress",
                topic: "memory.recall.insight",
                label: options.memoryInsight.summary,
                content: detailParts.join("\n"),
                data: {
                    runtimeId: "memory",
                    source: options.memoryInsight.source,
                    injectionAllowed: options.memoryInsight.injectionAllowed,
                    topScore: options.memoryInsight.topScore,
                    topScores: options.memoryInsight.topScores,
                    rejectReason: options.memoryInsight.rejectReason,
                    query: options.memoryInsight.query,
                },
                timestamp: options.memoryInsight.timestamp,
                agentName: "记忆召回",
                agentRoleLabel: "记忆召回",
            },
            kind: "progress",
            synthetic: true,
        });
    }

    activities.sort((left, right) => right.timestamp - left.timestamp);
    const realActivities = activities.filter((activity) => !activity.synthetic);
    const rawActiveRuntimeId = normalizePhoneRuntimeId(options?.ownerRuntime) ?? realActivities[0]?.runtimeId ?? null;
    const firstVisibleRuntimeWithActivity = realActivities.find((activity) => VISIBLE_PHONE_RUNTIME_ORDER.includes(activity.runtimeId))?.runtimeId ?? null;
    const activeRuntimeId = rawActiveRuntimeId && VISIBLE_PHONE_RUNTIME_ORDER.includes(rawActiveRuntimeId)
        ? rawActiveRuntimeId
        : firstVisibleRuntimeWithActivity || "chat";
    const runtimeStatus = String(options?.status || "").trim().toLowerCase();
    const isBusy = Boolean(runtimeStatus && !["completed", "failed", "cancelled", "idle"].includes(runtimeStatus));

    const items = VISIBLE_PHONE_RUNTIME_ORDER.map((runtimeId) => {
        const descriptor = getPhoneRuntimeDescriptor(runtimeId, options?.locale);
        const runtimeActivities = activities.filter((activity) => activity.runtimeId === runtimeId);
        const lastActivity = runtimeActivities[0];

        let status: PhoneRuntimeCardStatus = "idle";
        if (runtimeId === activeRuntimeId && isBusy) {
            status = options?.pendingApproval ? "attention" : "active";
        } else if (runtimeId === activeRuntimeId && runtimeStatus === "failed") {
            status = "attention";
        } else if (lastActivity) {
            status = "recent";
        }

        return {
            id: runtimeId,
            label: descriptor.label,
            shortLabel: descriptor.shortLabel,
            description: descriptor.description,
            status,
            eventCount: runtimeActivities.length,
            lastActivity: runtimeId === activeRuntimeId && options?.currentStepTitle
                ? options.currentStepTitle
                : lastActivity?.summary,
            lastTimestamp: lastActivity?.timestamp,
            stepTitle: runtimeId === activeRuntimeId ? options?.currentStepTitle || undefined : undefined,
            pendingApproval: runtimeId === activeRuntimeId ? options?.pendingApproval : false,
        } satisfies PhoneRuntimeStageCard;
    });

    return {
        activeRuntimeId,
        items,
        activities,
    };
}
