import { Message, UiArtifactNode, UiExecutionNode, UiGovernanceNode, UiTimelineNode } from "@/store/chat-types";
import {
    buildAuthoritativeRuntimeTimelineEntryFromEvent,
    coerceAdminResourceRef,
    type ContextGovernanceDigest,
    type MemoryRuntimeInsight,
    getRuntimeRegistryEntry,
    isEffectiveContextGovernancePayload,
    normalizeAuthoritativeRuntimeTimeline,
    normalizeSessionRuntimeEvent,
    normalizeRuntimeId as normalizeSharedRuntimeId,
    SESSION_RUNTIME_ORDER,
    VISIBLE_SESSION_RUNTIME_ORDER,
    type AuthoritativeRuntimeTimelineEntry,
    type SessionRuntimeId,
} from "@v8/session-realtime";
import type { Locale } from "@/lib/locale";

export type RuntimeId = SessionRuntimeId | "context_governance";
export type RuntimeCardStatus = "active" | "attention" | "recent" | "idle";

export interface RuntimeDescriptor {
    id: RuntimeId;
    label: string;
    shortLabel: string;
    description: string;
}

export interface RuntimeStageActivity {
    id: string;
    runtimeId: RuntimeId;
    timestamp: number;
    summary: string;
    topic?: string;
    actorLabel?: string;
    messageId: string;
    node: UiTimelineNode;
    kind: "progress" | "tool" | "governance" | "artifact" | "handoff";
    synthetic?: boolean;
    compactedCount?: number;
}

export type RuntimeTimelineEntry = AuthoritativeRuntimeTimelineEntry;

export interface RuntimeStageCard {
    id: RuntimeId;
    label: string;
    shortLabel: string;
    description: string;
    status: RuntimeCardStatus;
    eventCount: number;
    lastActivity?: string;
    lastTimestamp?: number;
    stepTitle?: string;
    pendingApproval?: boolean;
    recoverable?: boolean;
}

export interface RuntimeStageModel {
    activeRuntimeId: RuntimeId | null;
    items: RuntimeStageCard[];
    activities: RuntimeStageActivity[];
}

const CONTEXT_GOVERNANCE_DESCRIPTOR: Record<Locale, RuntimeDescriptor> = {
    "zh-CN": {
        id: "context_governance",
        label: "上下文治理",
        shortLabel: "治理",
        description: "查看上下文预算、压缩与召回注入。",
    },
    en: {
        id: "context_governance",
        label: "Context governance",
        shortLabel: "Govern",
        description: "Inspect context budget, compaction, and recall injection.",
    },
};

const RUNTIME_ORDER: RuntimeId[] = [...SESSION_RUNTIME_ORDER, "context_governance"];
const VISIBLE_RUNTIME_ORDER: RuntimeId[] = [...VISIBLE_SESSION_RUNTIME_ORDER, "context_governance"];

export function normalizeRuntimeId(raw?: string | null): RuntimeId | null {
    if (String(raw || "").trim().toLowerCase() === "context_governance") {
        return "context_governance";
    }
    return normalizeSharedRuntimeId(raw);
}

function firstRuntimeMatch(values: Array<string | null | undefined>): RuntimeId | null {
    for (const value of values) {
        const match = normalizeRuntimeId(value);
        if (match) return match;
    }
    return null;
}

function inferRuntimeIdFromArtifact(node: UiArtifactNode): RuntimeId | null {
    const artifact = node.artifact;
    const metadata = artifact.metadata as Record<string, unknown> | undefined;
    return firstRuntimeMatch([
        typeof metadata?.runtime === "string" ? metadata.runtime : undefined,
        typeof metadata?.runtimeId === "string" ? metadata.runtimeId : undefined,
        artifact.sourcePath,
        artifact.workspacePath,
        artifact.previewUrl,
        artifact.id,
    ]);
}

export function inferRuntimeIdFromNode(node: UiTimelineNode): RuntimeId | null {
    if (node.kind === "artifact") {
        return inferRuntimeIdFromArtifact(node);
    }

    if (node.kind === "execution") {
        const executionNode = node as UiExecutionNode;
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
        const governanceNode = node as UiGovernanceNode;
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

function summarizeExecutionNode(node: UiExecutionNode): string | null {
    if (node.executionType === "runtime_progress") {
        return node.label || node.topic || "运行中";
    }
    if (node.executionType === "tool_call") {
        return node.toolName ? `调用 ${node.toolName}` : "工具调用";
    }
    if (node.executionType === "tool_result") {
        return node.toolName ? `${node.toolName} 已完成` : (node.toolCallId ? `工具结果 ${node.toolCallId}` : "工具结果");
    }
    if (node.executionType === "agent_start") {
        return node.agentName ? `${node.agentName} 已接入` : "协作单元已接入";
    }
    if (node.executionType === "reasoning") {
        return "正在推理";
    }
    return null;
}

function summarizeGovernanceNode(node: UiGovernanceNode): string | null {
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

function summarizeArtifactNode(node: UiArtifactNode): string | null {
    return node.artifact.displayLabel || node.artifact.title || node.artifact.id || "新的产物";
}

function summarizeNode(node: UiTimelineNode): { summary: string; kind: RuntimeStageActivity["kind"] } | null {
    if (node.kind === "execution") {
        const summary = summarizeExecutionNode(node);
        if (!summary) return null;
        return {
            summary,
            kind: node.executionType === "tool_call" || node.executionType === "tool_result" ? "tool" : node.executionType === "agent_start" ? "handoff" : "progress",
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

function getNodeTimestamp(message: Message, node: UiTimelineNode): number {
    if (typeof node.timestamp === "number" && Number.isFinite(node.timestamp)) {
        return node.timestamp;
    }
    return message.timestamp;
}

function coerceTimelineString(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function remapTimelineEntryRuntimeId(entry: RuntimeTimelineEntry): RuntimeId {
    const topic = String(entry.topic || "").trim().toLowerCase();
    if (topic.startsWith("context.") || topic === "context_governance_changed" || topic.startsWith("supervisor.graph.")) {
        return "context_governance";
    }
    return entry.runtimeId;
}

export function normalizeRuntimeTimeline(input: unknown[]): RuntimeTimelineEntry[] {
    return normalizeAuthoritativeRuntimeTimeline(input);
}

export function buildRuntimeTimelineEntryFromEvent(raw: unknown): RuntimeTimelineEntry | null {
    return buildAuthoritativeRuntimeTimelineEntryFromEvent(raw, { locale: "zh-CN" });
}

export function mergeRuntimeTimeline(
    current: RuntimeTimelineEntry[],
    incoming: RuntimeTimelineEntry[],
): RuntimeTimelineEntry[] {
    const map = new Map<string, RuntimeTimelineEntry>();
    for (const item of [...current, ...incoming]) {
        const key = `${item.id}:${item.seq}`;
        const existing = map.get(key);
        if (!existing || item.timestamp >= existing.timestamp) {
            map.set(key, item);
        }
    }
    return Array.from(map.values()).sort((left, right) => right.timestamp - left.timestamp);
}

function runtimeCardIdForActivity(activity: RuntimeStageActivity): RuntimeId {
    return activity.runtimeId === "subagent_swarm" ? "chat" : activity.runtimeId;
}

function runtimeActivitiesForCard(runtimeId: RuntimeId, activities: RuntimeStageActivity[]): RuntimeStageActivity[] {
    if (runtimeId === "chat") {
        return activities.filter((activity) => activity.runtimeId === "chat" || activity.runtimeId === "subagent_swarm");
    }
    return activities.filter((activity) => activity.runtimeId === runtimeId);
}

function buildNodeFromTimelineEntry(entry: RuntimeTimelineEntry): UiTimelineNode {
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
        } satisfies UiGovernanceNode;
    }

    if (entry.kind === "artifact") {
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
                artifactId: coerceTimelineString(metadata.artifactId) || artifactId,
                title: coerceTimelineString(metadata.title) || entry.summary,
                displayLabel: coerceTimelineString(metadata.displayLabel) || coerceTimelineString(metadata.title) || entry.summary,
                displaySubtitle: coerceTimelineString(metadata.displaySubtitle)
                    || coerceTimelineString(metadata.workspacePath)
                    || coerceTimelineString(metadata.sourcePath)
                    || coerceTimelineString(metadata.kind)
                    || "暂无路径信息",
                kind: coerceTimelineString(metadata.kind) || "file",
                mimeType: coerceTimelineString(metadata.mimeType) || "application/octet-stream",
                previewUrl: coerceTimelineString(metadata.previewUrl),
                externalUrl: coerceTimelineString(metadata.externalUrl),
                sourcePath: coerceTimelineString(metadata.sourcePath),
                workspacePath: coerceTimelineString(metadata.workspacePath),
                resourceRef: coerceAdminResourceRef(metadata.resourceRef || metadata.previewUrl || metadata.externalUrl),
                runId: entry.runId,
                metadata,
            },
        } satisfies UiArtifactNode;
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
    } satisfies UiExecutionNode;
}

export function formatRelativeRuntimeTime(timestamp?: number): string {
    if (!timestamp) return "刚刚";
    const diffMs = Date.now() - timestamp;
    const diffMinutes = Math.max(0, Math.floor(diffMs / 60000));
    if (diffMinutes < 1) return "刚刚";
    if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours} 小时前`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays} 天前`;
}

export function getRuntimeDescriptor(runtimeId: RuntimeId, locale: Locale = "zh-CN"): RuntimeDescriptor {
    if (runtimeId === "context_governance") {
        return CONTEXT_GOVERNANCE_DESCRIPTOR[locale];
    }
    return getRuntimeRegistryEntry(runtimeId, locale);
}

export function getRuntimeDescriptors(locale: Locale = "zh-CN"): RuntimeDescriptor[] {
    return VISIBLE_RUNTIME_ORDER.map((runtimeId) => getRuntimeDescriptor(runtimeId, locale));
}

interface BuildRuntimeStageModelOptions {
    ownerRuntime?: string | null;
    status?: string | null;
    pendingApproval?: boolean;
    recoverable?: boolean;
    currentStepTitle?: string | null;
    runtimeTimeline?: RuntimeTimelineEntry[] | null;
    memoryInsight?: MemoryRuntimeInsight | null;
    governanceDigest?: ContextGovernanceDigest | null;
    governanceHistory?: ContextGovernanceDigest[] | null;
    locale?: Locale;
}

export function buildRuntimeStageModel(
    messages: Message[],
    options?: BuildRuntimeStageModelOptions,
): RuntimeStageModel {
    const activities: RuntimeStageActivity[] = [];

    for (const message of messages) {
        for (const node of message.nodes || []) {
            const runtimeId = inferRuntimeIdFromNode(node);
            const summarized = summarizeNode(node);
            if (!runtimeId || !summarized) continue;
            if (
                runtimeId === "chat"
                && node.kind === "execution"
                && (node.executionType === "tool_call" || node.executionType === "tool_result")
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
        const key = `${entry.id}:${entry.seq}`;
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
            kind: entry.kind,
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
            ? `上下文治理：${item.triggerReason}`
            : "上下文治理已更新";
        const detailParts = [
            item.resolvedScope ? `Scope: ${item.resolvedScope}` : null,
            typeof item.blockCount === "number" ? `Blocks: ${item.blockCount}` : null,
            item.durableFlushReason ? `Durable: ${item.durableFlushReason}` : null,
            item.compactionApplied ? "已压缩" : null,
            item.recallAudit?.rejectReason ? `Recall: ${item.recallAudit.rejectReason}` : null,
        ].filter((value): value is string => Boolean(value));
        const timestamp = item.eventTs ? Date.parse(item.eventTs) || Date.now() : Date.now();
        activities.push({
            id: `governance:${item.id}`,
            runtimeId: "context_governance",
            timestamp,
            summary,
            topic: "context.prepared",
            actorLabel: "上下文治理",
            messageId: `governance:${item.id}`,
            node: {
                id: `timeline-node-governance:${item.id}`,
                kind: "governance",
                governanceType: "context_governance",
                topic: "context.prepared",
                reason: detailParts.join("\n") || summary,
                status: item.compactionApplied ? "compacted" : "updated",
                timestamp,
                agentName: "上下文治理",
                agentRoleLabel: "上下文治理",
            },
            kind: "governance",
            synthetic: true,
        });
    }

    activities.sort((left, right) => right.timestamp - left.timestamp);

    const realActivities = activities.filter((activity) => !activity.synthetic);
    const normalizedOwnerRuntime = normalizeRuntimeId(options?.ownerRuntime);
    const rawActiveRuntimeId = normalizedOwnerRuntime === "subagent_swarm"
        ? "chat"
        : normalizedOwnerRuntime ?? realActivities[0]?.runtimeId ?? null;
    const runtimeActivitiesById = new Map<RuntimeId, RuntimeStageActivity[]>();
    const visibleRuntimeOrder = VISIBLE_RUNTIME_ORDER.filter((runtimeId) => {
        const runtimeActivities = runtimeActivitiesForCard(runtimeId, activities);
        if (runtimeActivities.length === 0) {
            return false;
        }
        runtimeActivitiesById.set(runtimeId, runtimeActivities);
        return true;
    });
    const firstVisibleRuntimeWithActivity = activities
        .map(runtimeCardIdForActivity)
        .find((runtimeId) => runtimeActivitiesById.has(runtimeId)) ?? null;
    const activeRuntimeId = rawActiveRuntimeId && runtimeActivitiesById.has(rawActiveRuntimeId)
        ? rawActiveRuntimeId
        : firstVisibleRuntimeWithActivity;
    const runtimeStatus = String(options?.status || "").trim().toLowerCase();
    const isBusy = Boolean(runtimeStatus && !["completed", "failed", "cancelled", "idle"].includes(runtimeStatus));

    const items = visibleRuntimeOrder.map((runtimeId) => {
        const descriptor = getRuntimeDescriptor(runtimeId, options?.locale || "zh-CN");
        const runtimeActivities = runtimeActivitiesById.get(runtimeId) || [];
        const lastActivity = runtimeActivities[0];

        let status: RuntimeCardStatus = "idle";
        if (runtimeId === activeRuntimeId && isBusy) {
            status = options?.pendingApproval ? "attention" : "active";
        } else if (runtimeId === activeRuntimeId && (options?.recoverable || String(options?.status || "").trim().toLowerCase() === "failed")) {
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
            recoverable: runtimeId === activeRuntimeId ? options?.recoverable : false,
        } satisfies RuntimeStageCard;
    });

    return {
        activeRuntimeId,
        items,
        activities,
    };
}
