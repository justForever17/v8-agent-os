import type {
    ChatArtifact,
    ChatMessage,
    PhoneUiArtifactNode,
    PhoneUiExecutionNode,
    PhoneUiGovernanceNode,
    PhoneUiTimelineNode,
} from "@/src/types/admin";
import { createTranslator } from "@/src/lib/locale";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import {
    buildAuthoritativeRuntimeTimelineEntryFromEvent,
    coerceAdminResourceRef,
    type ContextGovernanceDigest,
    type MemoryRuntimeInsight,
    getRuntimeRegistryEntry,
    isEffectiveContextGovernancePayload,
    isRuntimeEpisodeGraphActivity,
    normalizeAuthoritativeRuntimeTimeline,
    normalizeSessionRuntimeEvent,
    normalizeRuntimeId as normalizeSharedRuntimeId,
    SESSION_RUNTIME_ORDER,
    VISIBLE_SESSION_RUNTIME_ORDER,
    type AuthoritativeRuntimeTimelineEntry,
    type SessionRuntimeId,
} from "@v8/session-realtime";

export type PhoneRuntimeId =
    SessionRuntimeId | "context_governance";

export type PhoneRuntimeCardStatus = "idle" | "recent" | "active" | "attention";

export type PhoneRuntimeTimelineEntry = Omit<AuthoritativeRuntimeTimelineEntry, "runtimeId"> & {
    runtimeId: PhoneRuntimeId;
};

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

export const PHONE_RUNTIME_ORDER: PhoneRuntimeId[] = [
    "chat",
    "planner_lane",
    "engineering",
    "engineering_lane",
    "research",
    "extensions",
    "automation",
    "memory",
    "context_governance",
    "computer_use",
    "network_supervisor",
    "plugin_host_tool",
    "plugin_host_channel",
    "rpa",
    "desktop_live",
];

export const VISIBLE_PHONE_RUNTIME_ORDER: PhoneRuntimeId[] = [
    "chat",
    "engineering",
    "extensions",
    "automation",
    "memory",
    "context_governance",
    ...VISIBLE_SESSION_RUNTIME_ORDER.filter((runtimeId) => !["chat", "planner_lane", "engineering", "engineering_lane", "extensions", "automation", "memory"].includes(runtimeId)),
];

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
        if (governanceNode.governanceType === "context_governance") {
            return "context_governance";
        }
        return firstRuntimeMatch([
            governanceNode.topic,
            governanceNode.reason,
            governanceNode.agentName,
            governanceNode.agentRoleLabel,
        ]);
    }

    return firstRuntimeMatch([node.agentName, node.agentRoleLabel]);
}

function summarizeExecutionNode(node: PhoneUiExecutionNode, locale: LocaleCode = "zh-CN"): string | null {
    const t = createTranslator(locale);
    if (node.executionType === "runtime_progress") {
        return node.label || node.topic || t("src.lib.runtime_stage.running");
    }
    if (node.executionType === "tool_call") {
        return node.toolName
            ? t("src.lib.runtime_stage.call_tool", { toolName: node.toolName })
            : t("src.lib.runtime_stage.tool_call");
    }
    if (node.executionType === "tool_result") {
        if (node.toolName) {
            return t("src.lib.runtime_stage.tool_finished", { toolName: node.toolName });
        }
        return node.toolCallId
            ? t("src.lib.runtime_stage.tool_result_with_id", { toolCallId: node.toolCallId })
            : t("src.lib.runtime_stage.tool_result");
    }
    if (node.executionType === "agent_start") {
        return node.agentName
            ? t("src.lib.runtime_stage.agent_joined", { agentName: node.agentName })
            : t("src.lib.runtime_stage.collaboration_unit_joined");
    }
    if (node.executionType === "reasoning") {
        return t("src.lib.runtime_stage.reasoning");
    }
    return null;
}

function summarizeGovernanceNode(node: PhoneUiGovernanceNode, locale: LocaleCode = "zh-CN"): string | null {
    const t = createTranslator(locale);
    if (node.governanceType === "ask_user") {
        return node.question || t("src.lib.runtime_stage.waiting_for_your_input");
    }
    if (node.governanceType === "approval_request") {
        return node.question || t("src.lib.runtime_stage.waiting_for_approval");
    }
    if (node.governanceType === "approval_resolved") {
        return node.reason || node.status || t("src.lib.runtime_stage.approval_status_updated");
    }
    if (node.governanceType === "safety_blocked") {
        return node.reason || t("src.lib.runtime_stage.safety_precheck_blocked");
    }
    if (node.governanceType === "context_governance") {
        return node.reason || t("src.lib.runtime_stage.context_governance_updated");
    }
    if (node.governanceType === "lane_updated") {
        return node.reason || t("src.lib.runtime_stage.runtime_scheduling_updated");
    }
    return node.reason || node.status || node.topic || t("src.lib.runtime_stage.runtime_control_updated");
}

function summarizeTimelineNode(
    node: PhoneUiTimelineNode,
    locale: LocaleCode = "zh-CN",
): { summary: string; kind: PhoneRuntimeStageActivity["kind"] } | null {
    if (node.kind === "execution") {
        const summary = summarizeExecutionNode(node, locale);
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
        const summary = summarizeGovernanceNode(node, locale);
        if (!summary) return null;
        return { summary, kind: "governance" };
    }

    if (node.kind === "artifact") {
        return null;
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

function remapTimelineEntryRuntimeId(entry: PhoneRuntimeTimelineEntry): PhoneRuntimeId {
    const topic = String(entry.topic || "").trim().toLowerCase();
    if (topic.startsWith("context.") || topic === "context_governance_changed" || topic.startsWith("supervisor.graph.")) {
        return "context_governance";
    }
    return entry.runtimeId;
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
            resourceRef: coerceAdminResourceRef(metadata.resourceRef || metadata.previewUrl || metadata.externalUrl),
            runId: entry.runId,
            metadata,
        },
    };
}

function buildNodeFromTimelineEntry(entry: PhoneRuntimeTimelineEntry): PhoneUiTimelineNode {
    const governanceType = (() => {
        const topic = String(entry.topic || "").trim().toLowerCase();
        if (topic === "ask_user.requested") {
            return "ask_user" as const;
        }
        if (topic === "approval.requested") {
            return "approval_request" as const;
        }
        if (topic === "ask_user.resolved") return "ask_user" as const;
        if (topic.startsWith("approval.")) return "approval_resolved" as const;
        if (topic.startsWith("safety.")) return "safety_blocked" as const;
        if (topic.startsWith("context.") || topic.startsWith("supervisor.graph.")) return "context_governance" as const;
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
    const isToolResult = entry.kind === "tool" && (
        String(entry.topic || "").trim().toLowerCase() === "tool.finished"
        || metadata?.result !== undefined
        || metadata?.response !== undefined
        || metadata?.result_preview !== undefined
    );
    const executionType = entry.kind === "tool"
        ? (isToolResult ? "tool_result" : "tool_call")
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
        toolName: entry.kind === "tool" ? coerceTimelineString(metadata?.toolName || metadata?.tool_name) : undefined,
        toolCallId: entry.kind === "tool" ? coerceTimelineString(metadata?.toolCallId || metadata?.tool_call_id || metadata?.approval_id) : undefined,
        args: executionType === "tool_call" ? metadata?.args ?? metadata?.request : undefined,
        result: executionType === "tool_result" ? metadata?.result ?? metadata?.response ?? metadata?.result_preview : undefined,
        data: entry.metadata,
        timestamp: entry.timestamp,
        agentName: entry.actorLabel,
        agentRoleLabel: entry.actorLabel,
    };
}

export function formatPhoneRelativeRuntimeTime(
    timestamp?: number,
    locale: LocaleCode = "zh-CN",
    nowMs = Date.now(),
): string {
    const t = createTranslator(locale);
    if (!timestamp) return t("shared.time.just_now");
    const diffMs = nowMs - timestamp;
    const diffMinutes = Math.max(0, Math.floor(diffMs / 60000));
    if (diffMinutes < 1) return t("shared.time.just_now");
    if (diffMinutes < 60) return t("shared.time.minutes_ago", { count: diffMinutes });
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return t("shared.time.hours_ago", { count: diffHours });
    const diffDays = Math.floor(diffHours / 24);
    return t("shared.time.days_ago", { count: diffDays });
}

export function normalizePhoneRuntimeId(raw?: string | null): PhoneRuntimeId | null {
    const normalized = String(raw || "").trim().toLowerCase();
    if (!normalized) {
        return null;
    }
    if (normalized === "context_governance") {
        return "context_governance";
    }
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
    if (runtimeId === "context_governance") {
        const t = createTranslator(locale);
        return {
            id: runtimeId,
            label: t("src.lib.runtime_stage.context_governance_label"),
            shortLabel: t("src.lib.runtime_stage.context_governance_short_label"),
            description: t("src.lib.runtime_stage.context_governance_description"),
        };
    }
    const descriptor = getRuntimeRegistryEntry(runtimeId, locale);
    return {
        id: runtimeId,
        label: descriptor.label,
        shortLabel: descriptor.shortLabel,
        description: descriptor.description,
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

function isPhoneRuntimeEpisodeGraphActivity(activity: PhoneRuntimeStageActivity): boolean {
    return isRuntimeEpisodeGraphActivity({
        topic: activity.topic || ("topic" in activity.node ? String(activity.node.topic || "") : ""),
    });
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
        governanceDigest?: ContextGovernanceDigest | null;
        governanceHistory?: ContextGovernanceDigest[] | null;
        locale?: LocaleCode;
    },
): PhoneRuntimeStageModel {
    const locale = options?.locale || "zh-CN";
    const t = createTranslator(locale);
    const activities: PhoneRuntimeStageActivity[] = [];

    for (const message of messages) {
        for (const node of Array.isArray(message.nodes) ? message.nodes : []) {
            const runtimeId = inferPhoneRuntimeIdFromNode(node);
            const summarized = summarizeTimelineNode(node, locale);
            if (!runtimeId || !summarized) continue;
            if (
                runtimeId === "chat"
                && node.kind === "execution"
                && (node.executionType === "tool_call" || node.executionType === "tool_result" || node.executionType === "reasoning")
            ) {
                continue;
            }

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
        if (entry.kind === "artifact") {
            continue;
        }
        const key = `${entry.id}:${entry.seq || 0}`;
        if (seenTimelineKeys.has(key)) {
            continue;
        }
        seenTimelineKeys.add(key);
        const remappedRuntimeId = remapTimelineEntryRuntimeId(entry);
        if (remappedRuntimeId === "chat" && entry.kind === "tool") {
            continue;
        }
        activities.push({
            id: entry.id,
            runtimeId: remappedRuntimeId,
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
            ? t("src.lib.runtime_stage.top_scores", {
                scores: options.memoryInsight.topScores.slice(0, 3).map((score) => score.toFixed(2)).join(", "),
            })
            : null;
        const detailParts = [
            options.memoryInsight.query ? t("src.lib.runtime_stage.query_detail", { query: options.memoryInsight.query }) : null,
            options.memoryInsight.rejectReason ? t("src.lib.runtime_stage.reject_detail", { reason: options.memoryInsight.rejectReason }) : null,
            topScoresLabel,
        ].filter((item): item is string => Boolean(item));
        activities.push({
            id: options.memoryInsight.id,
            runtimeId: "memory",
            timestamp: options.memoryInsight.timestamp,
            summary: options.memoryInsight.summary,
            topic: "memory.recall.insight",
            actorLabel: t("src.lib.runtime_stage.memory_recall"),
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
                agentName: t("src.lib.runtime_stage.memory_recall"),
                agentRoleLabel: t("src.lib.runtime_stage.memory_recall"),
            },
            kind: "progress",
            synthetic: true,
        });
    }

    const governanceDigests = [
        options?.governanceDigest || null,
        ...(options?.governanceHistory || []),
    ].filter((item): item is ContextGovernanceDigest => Boolean(item) && isEffectiveContextGovernancePayload(item));
    const seenGovernanceIds = new Set<string>();
    for (const item of governanceDigests) {
        if (!item.id || seenGovernanceIds.has(item.id)) {
            continue;
        }
        seenGovernanceIds.add(item.id);
        const summary = item.triggerReason
            ? t("src.lib.runtime_stage.context_governance_reason", { reason: item.triggerReason })
            : t("src.lib.runtime_stage.context_governance_updated_summary");
        const detailParts = [
            item.resolvedScope ? t("src.lib.runtime_stage.scope_detail", { scope: item.resolvedScope }) : null,
            typeof item.blockCount === "number" ? t("src.lib.runtime_stage.blocks_detail", { count: item.blockCount }) : null,
            item.durableFlushReason ? t("src.lib.runtime_stage.durable_detail", { reason: item.durableFlushReason }) : null,
            item.compactionApplied ? t("src.lib.runtime_stage.compaction_applied") : null,
            item.recallAudit?.rejectReason ? t("src.lib.runtime_stage.recall_detail", { reason: item.recallAudit.rejectReason }) : null,
        ].filter((value): value is string => Boolean(value));
        activities.push({
            id: `governance:${item.id}`,
            runtimeId: "context_governance",
            timestamp: item.eventTs ? Date.parse(item.eventTs) || Date.now() : Date.now(),
            summary,
            topic: "context.prepared",
            actorLabel: t("src.lib.runtime_stage.context_governance_label"),
            messageId: `governance:${item.id}`,
            node: {
                id: `timeline-node-governance:${item.id}`,
                kind: "governance",
                governanceType: "context_governance",
                topic: "context.prepared",
                reason: detailParts.join("\n") || summary,
                status: item.compactionApplied ? "compacted" : "updated",
                timestamp: item.eventTs ? Date.parse(item.eventTs) || Date.now() : Date.now(),
                agentName: t("src.lib.runtime_stage.context_governance_label"),
                agentRoleLabel: t("src.lib.runtime_stage.context_governance_label"),
            },
            kind: "governance",
            synthetic: true,
        });
    }

    activities.sort((left, right) => right.timestamp - left.timestamp);
    const realActivities = activities.filter((activity) => !activity.synthetic);
    const normalizedOwnerRuntime = normalizePhoneRuntimeId(options?.ownerRuntime);
    const rawActiveRuntimeId = normalizedOwnerRuntime === "subagent_swarm"
        ? "chat"
        : normalizedOwnerRuntime ?? realActivities[0]?.runtimeId ?? null;
    const firstVisibleRuntimeWithActivity = realActivities.find((activity) => VISIBLE_PHONE_RUNTIME_ORDER.includes(activity.runtimeId))?.runtimeId ?? null;
    const activeRuntimeId = rawActiveRuntimeId && VISIBLE_PHONE_RUNTIME_ORDER.includes(rawActiveRuntimeId)
        ? rawActiveRuntimeId
        : firstVisibleRuntimeWithActivity || "chat";
    const runtimeStatus = String(options?.status || "").trim().toLowerCase();
    const isBusy = Boolean(runtimeStatus && !["completed", "failed", "cancelled", "idle"].includes(runtimeStatus));

    const visibleRuntimeOrder = VISIBLE_PHONE_RUNTIME_ORDER.filter((runtimeId) => (
        runtimeId !== "context_governance"
        || activities.some((activity) => activity.runtimeId === "context_governance")
    ));

    const items = visibleRuntimeOrder.map((runtimeId) => {
        const descriptor = getPhoneRuntimeDescriptor(runtimeId, options?.locale);
        const runtimeActivities = runtimeId === "chat"
            ? activities.filter((activity) => activity.runtimeId === "chat" || activity.runtimeId === "subagent_swarm" || isPhoneRuntimeEpisodeGraphActivity(activity))
            : activities.filter((activity) => activity.runtimeId === runtimeId);
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
