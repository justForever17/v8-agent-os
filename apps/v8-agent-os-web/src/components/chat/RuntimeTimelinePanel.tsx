"use client";

import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
    buildRuntimeEpisodeGraph,
    isRuntimeEpisodeGraphActivity,
    type AdminProcessRef,
    type ContextGovernanceView,
    type RuntimeEpisodeGraphActivity,
    normalizeContextGovernanceDigest,
    normalizeContextGovernanceHistory,
} from "@v8/session-realtime";
import { cn } from "@/lib/utils";
import {
    RuntimeId,
    RuntimeStageActivity,
    RuntimeStageModel,
    formatRelativeRuntimeTime,
    getRuntimeDescriptor,
} from "@/lib/runtime-stage";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { ContentDispatcher } from "./ContentDispatcher";
import { Activity, AlertTriangle, Blocks, Bot, Box, Code2, Cpu, Database, GitBranch, Globe, RadioTower, Shield, Sparkles, TerminalSquare, Workflow, X } from "lucide-react";

interface RuntimeTimelinePanelProps {
    isOpen: boolean;
    onClose: () => void;
    model: RuntimeStageModel;
    selectedRuntimeId: RuntimeId | null;
    processes: AdminProcessRef[];
    overallStatus?: string;
    currentStepTitle?: string | null;
    pendingApproval?: boolean;
    contextGovernance?: ContextGovernanceView | null;
    contextGovernanceHistory?: ContextGovernanceView[];
    onSelectRuntime: (runtimeId: RuntimeId) => void;
}

const runtimeIcons: Record<RuntimeId, React.ElementType<{ className?: string }>> = {
    chat: Bot,
    engineering: Code2,
    engineering_lane: Code2,
    research: Globe,
    extensions: Blocks,
    creative_media: Sparkles,
    automation: Workflow,
    memory: Database,
    context_governance: Shield,
    subagent_swarm: GitBranch,
    network_supervisor: Globe,
    computer_use: TerminalSquare,
    rpa: Cpu,
    desktop_live: RadioTower,
};

const kindMeta: Record<RuntimeStageActivity["kind"], { label: string; icon: React.ElementType<{ className?: string }>; tone: string }> = {
    progress: { label: "运行", icon: Activity, tone: "text-emerald-600 dark:text-emerald-300" },
    tool: { label: "工具", icon: TerminalSquare, tone: "text-sky-600 dark:text-sky-300" },
    governance: { label: "控制", icon: AlertTriangle, tone: "text-rose-600 dark:text-rose-300" },
    artifact: { label: "产物", icon: Box, tone: "text-violet-600 dark:text-violet-300" },
    handoff: { label: "交接", icon: Workflow, tone: "text-amber-600 dark:text-amber-300" },
};

function readExecutionData(activity: RuntimeStageActivity): Record<string, unknown> {
    if (activity.node.kind !== "execution" || !activity.node.data || typeof activity.node.data !== "object") {
        return {};
    }
    return activity.node.data as Record<string, unknown>;
}

const CHAT_RUNTIME_ACTIVITY_WINDOW = 80;
const RUNTIME_ACTIVITY_FEED_LIMIT = 40;

function readString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function readRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function normalizeActivitySummary(value: string): string {
    return value
        .trim()
        .toLowerCase()
        .replace(/[a-f0-9]{8,}/g, "#")
        .replace(/\d+(?:\.\d+)?/g, "#")
        .replace(/\s+/g, " ")
        .slice(0, 160);
}

function readActivityStatus(activity: RuntimeStageActivity): string {
    const data = readExecutionData(activity);
    const nodeStatus = "status" in activity.node ? activity.node.status : undefined;
    return readString(nodeStatus)
        || readString(data.status)
        || readString(data.state)
        || readString(data.phase)
        || "";
}

function getActivityCompactKey(activity: RuntimeStageActivity): string {
    const data = readExecutionData(activity);
    const dedupeKey = readString(data.dedupeKey) || readString(data.dedupe_key);
    if (dedupeKey) {
        return `${activity.runtimeId}:dedupe:${dedupeKey}`;
    }
    return [
        activity.runtimeId,
        activity.topic || "",
        activity.kind,
        readActivityStatus(activity),
        normalizeActivitySummary(activity.summary),
    ].join("|");
}

function compactRuntimeActivities(
    activities: RuntimeStageActivity[],
    limit = RUNTIME_ACTIVITY_FEED_LIMIT,
): RuntimeStageActivity[] {
    const compacted: RuntimeStageActivity[] = [];
    const indexByKey = new Map<string, number>();
    for (const activity of activities) {
        const key = getActivityCompactKey(activity);
        const existingIndex = indexByKey.get(key);
        if (existingIndex === undefined) {
            indexByKey.set(key, compacted.length);
            compacted.push({ ...activity, compactedCount: activity.compactedCount || 1 });
            continue;
        }
        const existing = compacted[existingIndex];
        existing.compactedCount = (existing.compactedCount || 1) + (activity.compactedCount || 1);
    }
    return compacted.slice(0, limit);
}

function getSwarmTaskBriefId(activity?: RuntimeStageActivity): string {
    if (!activity) return "";
    const data = readExecutionData(activity);
    return readString(data.taskBriefId)
        || readString(data.task_brief_id)
        || readString(data.invocationId)
        || activity.id;
}

function getEngineeringGroupId(activity?: RuntimeStageActivity): string {
    if (!activity) return "";
    const data = readExecutionData(activity);
    return readString(data.taskBriefId)
        || readString(data.proofEntryId)
        || readString(data.planId)
        || readString((readRecord(data.traceRef)).runId)
        || activity.id;
}

function getEngineeringLabel(activity: RuntimeStageActivity): { title: string; meta: string } {
    const data = readExecutionData(activity);
    const worksetDecision = readRecord(data.worksetDispatchDecision);
    const title = readString(data.taskGoal)
        || readString(data.patchIntent)
        || readString(data.planSummary)
        || readString(data.summary)
        || activity.summary
        || "Engineering lane";
    const verificationStatus = readString(data.verificationStatus);
    const risk = readString(data.risk) || readString(worksetDecision.risk);
    const decisionSource = readString(data.worksetDecisionSource) || readString(data.decisionSource) || readString(worksetDecision.worksetDecisionSource);
    const ownershipCount = Number(data.ownershipCount || (Array.isArray(data.ownershipPlan) ? data.ownershipPlan.length : 0) || 0);
    const outsideCount = Number(data.outsideWriteSetCount || (Array.isArray(data.outsideWriteSetFiles) ? data.outsideWriteSetFiles.length : 0) || 0);
    const warningCount = Number(data.warningCount || 0);
    const blockedCount = Number(data.blockedCount || 0);
    const metaParts: string[] = [];
    if (verificationStatus) metaParts.push(verificationStatus);
    if (risk) metaParts.push(`risk: ${risk}`);
    if (decisionSource) metaParts.push(decisionSource);
    if (ownershipCount > 0) metaParts.push(`${ownershipCount} owners`);
    if (blockedCount > 0) {
        metaParts.push(`${blockedCount} blocked`);
    } else if (warningCount > 0) {
        metaParts.push(`${warningCount} warnings`);
    }
    if (outsideCount > 0) metaParts.push(`${outsideCount} outside`);
    return {
        title,
        meta: metaParts.join(" · ") || "engineering governance",
    };
}

function buildEpisodeKindLabels(t: ReturnType<typeof useT>): Record<string, string> {
    return {
        runtime: t("web.generated.53ef0e5627"),
        engineering: t("web.generated.80961bbc88"),
        research: t("web.generated.1e1e342d4a"),
        creative_media: t("web.generated.05dc567273"),
        computer_use: t("web.generated.07d3a36915"),
        rpa: t("web.generated.6ee7a4c326"),
        delegation: t("web.generated.0e62d69473"),
        handoff: t("web.generated.608efdd419"),
    };
}

function toRuntimeEpisodeGraphActivities(activities: RuntimeStageActivity[]): RuntimeEpisodeGraphActivity[] {
    return activities.map((activity) => ({
        id: activity.id,
        topic: activity.topic || ("topic" in activity.node ? String(activity.node.topic || "") : ""),
        summary: activity.summary,
        timestamp: activity.timestamp,
        data: readExecutionData(activity),
    }));
}

function isWebRuntimeEpisodeActivity(activity: RuntimeStageActivity): boolean {
    return isRuntimeEpisodeGraphActivity({
        topic: activity.topic || ("topic" in activity.node ? String(activity.node.topic || "") : ""),
    });
}

type SwarmNodeStatus = "active" | "completed" | "failed" | "pending" | "attempted";

type SwarmGraphNode = {
    id: string;
    parentId: string | null;
    label: string;
    subtitle: string;
    status: SwarmNodeStatus;
    depth: number;
    eventCount: number;
    timestamp: number;
};

function normalizeSwarmStatus(value: string): SwarmNodeStatus | null {
    const normalized = value.trim().toLowerCase();
    if (!normalized) return null;
    if (/(fail|error|reject|blocked|cancel)/.test(normalized)) return "failed";
    if (/(complete|finish|done|success|succeeded)/.test(normalized)) return "completed";
    if (/(attempt|revealed|missing|no_task|no-task|no_tasks|no-tasks|unconfirmed)/.test(normalized)) return "attempted";
    if (/(start|dispatch|run|active|progress|pending|queued)/.test(normalized)) return "active";
    return null;
}

function inferSwarmStatus(activity: RuntimeStageActivity, data: Record<string, unknown>): SwarmNodeStatus {
    const explicit = normalizeSwarmStatus(readString(data.status) || readString(data.state) || readString(data.phase));
    if (explicit) return explicit;
    const topicStatus = normalizeSwarmStatus(`${activity.topic || ""} ${activity.summary || ""}`);
    if (topicStatus) return topicStatus;
    if (activity.kind === "tool" || activity.kind === "handoff" || activity.kind === "progress") return "active";
    return "pending";
}

function getSwarmNodeIdFromData(activity: RuntimeStageActivity, data: Record<string, unknown>) {
    return readString(data.delegationId)
        || readString(data.delegation_id)
        || readString(data.invocationId)
        || readString(data.invocation_id)
        || readString(data.subagentId)
        || readString(data.subagent_id)
        || readString(data.agentId)
        || readString(data.agent_id)
        || getSwarmTaskBriefId(activity);
}

function getSwarmParentIdFromData(data: Record<string, unknown>) {
    return readString(data.parentDelegationId)
        || readString(data.parent_delegation_id)
        || readString(data.parentInvocationId)
        || readString(data.parent_invocation_id)
        || readString(data.parentTaskBriefId)
        || readString(data.parent_task_brief_id)
        || null;
}

function getSwarmNodeLabel(activity: RuntimeStageActivity, data: Record<string, unknown>) {
    const lane = readString(data.lane);
    return readString(data.subagentName)
        || readString(data.subagent_name)
        || readString(data.targetLabel)
        || readString(data.target_label)
        || readString(data.workerType)
        || readString(data.worker_type)
        || readString(data.agentName)
        || readString(data.agent_name)
        || activity.actorLabel
        || (lane === "external_worker" ? "External worker" : "Subagent");
}

function buildSwarmGraph(activities: RuntimeStageActivity[]): SwarmGraphNode[] {
    const nodes = new Map<string, SwarmGraphNode>();
    nodes.set("supervisor", {
        id: "supervisor",
        parentId: null,
        label: "Supervisor",
        subtitle: "orchestrator",
        status: "active",
        depth: 0,
        eventCount: 0,
        timestamp: 0,
    });

    for (const activity of [...activities].reverse()) {
        const data = readExecutionData(activity);
        const id = getSwarmNodeIdFromData(activity, data);
        if (!id) continue;
        const parentId = getSwarmParentIdFromData(data) || "supervisor";
        const status = inferSwarmStatus(activity, data);
        const taskGoal = readString(data.taskGoal) || readString(data.task_goal) || readString(data.summary) || activity.summary;
        const existing = nodes.get(id);
        nodes.set(id, {
            id,
            parentId,
            label: existing?.label || getSwarmNodeLabel(activity, data),
            subtitle: taskGoal || existing?.subtitle || "",
            status: status === "pending" ? (existing?.status || "pending") : status,
            depth: existing?.depth || 1,
            eventCount: (existing?.eventCount || 0) + 1,
            timestamp: Math.max(existing?.timestamp || 0, activity.timestamp),
        });
    }

    const visited = new Set<string>();
    const resolveDepth = (id: string): number => {
        const node = nodes.get(id);
        if (!node || !node.parentId || node.parentId === id) return 0;
        if (visited.has(id)) return node.depth || 1;
        visited.add(id);
        const parentDepth = nodes.has(node.parentId) ? resolveDepth(node.parentId) : 0;
        node.depth = Math.min(8, parentDepth + 1);
        return node.depth;
    };

    for (const id of nodes.keys()) {
        resolveDepth(id);
    }

    return Array.from(nodes.values()).sort((left, right) => {
        if (left.depth !== right.depth) return left.depth - right.depth;
        return left.timestamp - right.timestamp;
    });
}

function SwarmNodeBoard({ activities }: { activities: RuntimeStageActivity[] }) {
    const nodes = React.useMemo(() => buildSwarmGraph(activities), [activities]);
    const visibleNodes = nodes.filter((node) => node.id !== "supervisor" || nodes.length > 1);
    const activeIds = new Set(nodes.filter((node) => node.status === "active").map((node) => node.id));
    const statusClass: Record<SwarmNodeStatus, string> = {
        active: "bg-sky-500 text-sky-700 dark:text-sky-300",
        completed: "bg-emerald-500 text-emerald-700 dark:text-emerald-300",
        failed: "bg-rose-500 text-rose-700 dark:text-rose-300",
        pending: "bg-stone-400 text-stone-500 dark:text-stone-300",
        attempted: "bg-amber-500 text-amber-700 dark:text-amber-300",
    };
    const statusLabel: Record<SwarmNodeStatus, string> = {
        active: "运行",
        completed: "完成",
        failed: "失败",
        pending: "等待",
        attempted: "未确认",
    };

    if (visibleNodes.length <= 1) {
        return (
            <div className="rounded-[20px] border border-dashed border-stone-300/80 bg-white/55 px-4 py-6 text-center text-sm leading-6 text-muted-foreground dark:border-white/10 dark:bg-white/[0.03]">
                当前还没有真实子代理派发。若 Supervisor 只是口头声称派发，这里不会伪造节点。
            </div>
        );
    }

    return (
        <div className="space-y-3">
            <div className="text-[13px] font-semibold tracking-tight text-foreground">实际派发拓扑</div>
            <div className="space-y-2.5">
                {visibleNodes.map((node) => {
                    const activeLine = Boolean(node.parentId && activeIds.has(node.id));
                    return (
                        <div key={node.id} className="flex min-h-[48px] items-center">
                            <div style={{ width: Math.min(node.depth, 6) * 22 }} />
                            {node.id !== "supervisor" && (
                                <div
                                    className={cn(
                                        "h-0.5 w-5 rounded-full",
                                        activeLine ? "bg-sky-400 shadow-[0_0_14px_rgba(56,189,248,0.65)]" : "bg-stone-300/70 dark:bg-white/15",
                                    )}
                                />
                            )}
                            <span
                                className={cn(
                                    "h-2.5 w-2.5 rounded-full shadow-[0_0_0_rgba(0,0,0,0)]",
                                    statusClass[node.status].split(" ")[0],
                                    node.status === "active" && "shadow-[0_0_16px_rgba(56,189,248,0.72)]",
                                )}
                            />
                            <div className="ml-2.5 min-w-0 flex-1 py-1">
                                <div className="flex min-w-0 items-center gap-2">
                                    <div className="truncate text-[13px] font-semibold text-foreground">{node.label}</div>
                                    <div className={cn("shrink-0 text-[10px] font-semibold uppercase", statusClass[node.status].split(" ").slice(1).join(" "))}>
                                        {statusLabel[node.status]}
                                    </div>
                                </div>
                                {node.subtitle && (
                                    <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                                        {node.subtitle}
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export function RuntimeEpisodeBoard({ activities }: { activities: RuntimeStageActivity[] }) {
    const t = useT();
    const episodeKindLabels = React.useMemo(() => buildEpisodeKindLabels(t), [t]);
    const nodes = React.useMemo(
        () => buildRuntimeEpisodeGraph(toRuntimeEpisodeGraphActivities(activities), {
            rootLabel: "Supervisor",
            kindLabels: episodeKindLabels,
        }),
        [activities, episodeKindLabels],
    );
    const visibleNodes = nodes.filter((node) => node.id !== "supervisor" || nodes.length > 1);
    const activeIds = new Set(nodes.filter((node) => node.status === "active").map((node) => node.id));
    const statusClass: Record<SwarmNodeStatus, string> = {
        active: "bg-sky-500 text-sky-700 dark:text-sky-300",
        completed: "bg-emerald-500 text-emerald-700 dark:text-emerald-300",
        failed: "bg-rose-500 text-rose-700 dark:text-rose-300",
        pending: "bg-stone-400 text-stone-500 dark:text-stone-300",
        attempted: "bg-amber-500 text-amber-700 dark:text-amber-300",
    };
    const statusLabel: Record<SwarmNodeStatus, string> = {
        active: t("web.generated.0832eae7ec"),
        completed: t("web.generated.23c91b78cd"),
        failed: t("web.generated.1dad4921f1"),
        pending: t("web.generated.bc00ed4da7"),
        attempted: t("web.generated.59c6a041d7"),
    };

    if (visibleNodes.length <= 1) return null;

    return (
        <div className="space-y-3 rounded-[22px] border border-stone-200/80 bg-white/86 p-3.5 shadow-[0_12px_32px_rgba(15,23,42,0.05)] dark:border-white/10 dark:bg-white/[0.03]">
            <div className="text-[13px] font-semibold tracking-tight text-foreground">{t("web.generated.c6e4556097")}</div>
            <div className="space-y-2.5">
                {visibleNodes.map((node) => {
                    const activeLine = Boolean(node.parentId && activeIds.has(node.id));
                    return (
                        <div key={node.id} className="flex min-h-[48px] items-center">
                            <div style={{ width: Math.min(node.depth, 6) * 22 }} />
                            {node.id !== "supervisor" && (
                                <div
                                    className={cn(
                                        "h-0.5 w-5 rounded-full",
                                        activeLine ? "bg-sky-400 shadow-[0_0_14px_rgba(56,189,248,0.65)]" : "bg-stone-300/70 dark:bg-white/15",
                                    )}
                                />
                            )}
                            <span
                                className={cn(
                                    "h-2.5 w-2.5 rounded-full shadow-[0_0_0_rgba(0,0,0,0)]",
                                    statusClass[node.status].split(" ")[0],
                                    node.status === "active" && "shadow-[0_0_16px_rgba(56,189,248,0.72)]",
                                )}
                            />
                            <div className="ml-2.5 min-w-0 flex-1 py-1">
                                <div className="flex min-w-0 items-center gap-2">
                                    <div className="truncate text-[13px] font-semibold text-foreground">{node.label}</div>
                                    <div className={cn("shrink-0 text-[10px] font-semibold uppercase", statusClass[node.status].split(" ").slice(1).join(" "))}>
                                        {statusLabel[node.status]}
                                    </div>
                                </div>
                                {node.subtitle && (
                                    <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                                        {node.subtitle}
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function BroadcastRail({ activities }: { activities: RuntimeStageActivity[] }) {
    const [index, setIndex] = React.useState(0);
    const rowHeight = 78;

    React.useEffect(() => {
        setIndex(0);
    }, [activities]);

    React.useEffect(() => {
        if (activities.length <= 1) return;
        const timer = window.setInterval(() => {
            setIndex((prev) => (prev + 1) % activities.length);
        }, 2600);
        return () => window.clearInterval(timer);
    }, [activities]);

    if (activities.length === 0) {
        return null;
    }

    const active = activities[index];
    const meta = kindMeta[active.kind];
    const Icon = meta.icon;
    const loopedActivities = activities.length > 1 ? [...activities, ...activities] : activities;

    return (
        <div className="relative overflow-hidden rounded-[22px] border border-stone-200/80 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.14),transparent_45%),linear-gradient(180deg,#151515_0%,#1f1b17_100%)] shadow-[0_20px_48px_rgba(15,23,42,0.16)] dark:border-white/10">
            <div className="flex items-center justify-between border-b border-white/10 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-stone-300">
                <div className="flex items-center gap-2">
                    <span className="relative flex h-2.5 w-2.5">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-80" />
                        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-400" />
                    </span>
                    <span>Broadcast</span>
                </div>
                <span>{activities.length} 条</span>
            </div>
            <div className="grid gap-2.5 px-3 py-3 md:grid-cols-[1.1fr_0.9fr]">
                <div className="relative h-[196px] overflow-hidden rounded-[20px] border border-white/10 bg-white/[0.04]">
                    <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-12 bg-gradient-to-b from-[#171717] via-[#171717]/80 to-transparent" />
                    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-12 bg-gradient-to-t from-[#171717] via-[#171717]/80 to-transparent" />
                    <motion.div
                        animate={{ y: -(index * rowHeight) }}
                        transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
                        className="px-2 py-1.5"
                    >
                        {loopedActivities.map((activity, itemIndex) => {
                            const itemMeta = kindMeta[activity.kind];
                            const ItemIcon = itemMeta.icon;
                            const isCurrent = itemIndex % activities.length === index;
                            return (
                                <div
                                    key={`${activity.id}-${itemIndex}`}
                                    className={cn(
                                        "flex h-[58px] items-center gap-2 rounded-[16px] px-2.5 transition-all duration-300",
                                        isCurrent ? "bg-white/[0.08]" : "opacity-60",
                                    )}
                                >
                                    <div className={cn("flex h-8 w-8 items-center justify-center rounded-full bg-white/10", itemMeta.tone)}>
                                        <ItemIcon className="h-3.5 w-3.5" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="truncate text-[13px] font-medium text-stone-100">
                                            {activity.summary}
                                        </div>
                                        <div className="mt-0.5 truncate text-[11px] text-stone-400">
                                            {activity.actorLabel || itemMeta.label} · {formatRelativeRuntimeTime(activity.timestamp)}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </motion.div>
                </div>

                <AnimatePresence mode="wait">
                    <motion.div
                        key={active.id}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -10 }}
                        transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                        className="flex min-h-[196px] flex-col justify-between rounded-[20px] border border-white/10 bg-white/[0.04] p-3"
                    >
                        <div>
                            <div className="flex items-center gap-2">
                                <div className={cn("flex h-8 w-8 items-center justify-center rounded-full bg-white/10", meta.tone)}>
                                    <Icon className="h-3.5 w-3.5" />
                                </div>
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-[13px] font-semibold text-stone-100">{active.summary}</div>
                                    <div className="mt-0.5 text-[11px] text-stone-400">
                                        {active.actorLabel || meta.label} · {formatRelativeRuntimeTime(active.timestamp)}
                                    </div>
                                </div>
                            </div>
                            <div className="mt-3 text-[13px] leading-6 text-stone-300">
                                {active.topic || "运行轨迹正在持续刷新。你可以继续停留在聊天主界面，细节会在这里循环播报。"}
                            </div>
                        </div>

                        <div className="rounded-[16px] border border-white/10 bg-black/10 px-3 py-2.5 text-[10px] uppercase tracking-[0.16em] text-stone-400">
                            {index + 1}/{activities.length} · now broadcasting
                        </div>
                    </motion.div>
                </AnimatePresence>
            </div>
        </div>
    );
}

function ActivityFeedItem({
    activity,
    processes,
}: {
    activity: RuntimeStageActivity;
    processes: AdminProcessRef[];
}) {
    const meta = kindMeta[activity.kind];
    const Icon = meta.icon;

    return (
        <div className="rounded-[22px] border border-stone-200/80 bg-white/90 p-3.5 shadow-[0_12px_32px_rgba(15,23,42,0.05)] dark:border-white/10 dark:bg-white/[0.03]">
            <div className="mb-2.5 flex flex-wrap items-center gap-2">
                <span className={cn("inline-flex items-center gap-1.5 rounded-full bg-stone-100 px-2.5 py-0.5 text-[10px] font-semibold dark:bg-white/5", meta.tone)}>
                    <Icon className="h-3.5 w-3.5" />
                    {meta.label}
                </span>
                {activity.actorLabel && (
                    <span className="rounded-full bg-stone-100 px-2.5 py-0.5 text-[10px] text-muted-foreground dark:bg-white/5">
                        {activity.actorLabel}
                    </span>
                )}
                {activity.compactedCount && activity.compactedCount > 1 && (
                    <span className="rounded-full bg-stone-100 px-2.5 py-0.5 text-[10px] text-muted-foreground dark:bg-white/5">
                        ×{activity.compactedCount}
                    </span>
                )}
                <span className="ml-auto text-[10px] text-muted-foreground">
                    {formatRelativeRuntimeTime(activity.timestamp)}
                </span>
            </div>

            <div className="mb-2.5 min-w-0 whitespace-pre-wrap break-all text-[13px] font-medium leading-6 text-foreground/90">
                {activity.summary}
            </div>

            <div className="rounded-[18px] border border-stone-200/70 bg-stone-50/85 p-2.5 dark:border-white/5 dark:bg-white/[0.025]">
                {activity.node.kind === "artifact" ? (
                    <div className="text-sm text-muted-foreground">产物已记录，可在 Artifacts 面板查看详细内容。</div>
                ) : (
                    <ContentDispatcher node={activity.node} isExecuting={false} isStreaming={false} processes={processes} />
                )}
            </div>
        </div>
    );
}

function ContextGovernanceSection({
    contextGovernance,
    contextGovernanceHistory,
}: {
    contextGovernance?: ContextGovernanceView | null;
    contextGovernanceHistory?: ContextGovernanceView[];
}) {
    const latest = React.useMemo(
        () => normalizeContextGovernanceDigest(contextGovernance || null),
        [contextGovernance],
    );
    const historyItems = React.useMemo(() => {
        const normalized = normalizeContextGovernanceHistory(contextGovernanceHistory || []);
        const trimmed = normalized.slice(-4).reverse();
        if (!latest) {
            return trimmed;
        }
        return trimmed.filter((item) => item.id !== latest.id);
    }, [contextGovernanceHistory, latest]);

    if (!latest && historyItems.length === 0) {
        return null;
    }

    const renderGovernanceCard = (
        item: ReturnType<typeof normalizeContextGovernanceDigest>,
        variant: "latest" | "history",
    ) => {
        if (!item) {
            return null;
        }
        const hasCompactionNote = item.compactionApplied || Boolean(item.compactionMethod) || item.estimatedSavedTokens;
        return (
            <div
                key={`${variant}:${item.id}`}
                className={cn(
                    "rounded-[20px] border p-3.5 shadow-[0_12px_32px_rgba(15,23,42,0.05)]",
                    variant === "latest"
                        ? "border-amber-200/80 bg-amber-50/90 dark:border-amber-500/20 dark:bg-amber-500/6"
                        : "border-stone-200/80 bg-white/85 dark:border-white/10 dark:bg-white/[0.03]",
                )}
            >
                <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-white/80 px-2.5 py-0.5 text-[10px] font-semibold text-stone-700 shadow-sm dark:bg-white/10 dark:text-stone-100">
                        {variant === "latest" ? "最近治理" : "治理记录"}
                    </span>
                    {item.runtimeKind && (
                        <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[10px] text-muted-foreground dark:bg-white/5">
                            {item.runtimeKind}
                        </span>
                    )}
                    {item.targetRole && (
                        <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[10px] text-muted-foreground dark:bg-white/5">
                            {item.targetRole}
                        </span>
                    )}
                    {item.eventTs && (
                        <span className="ml-auto text-[10px] text-muted-foreground">
                            {new Date(item.eventTs).toLocaleString()}
                        </span>
                    )}
                </div>

                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                    {item.resolvedScope && (
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 dark:bg-white/5">
                            Scope: {item.resolvedScope}
                        </span>
                    )}
                    {typeof item.blockCount === "number" && (
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 dark:bg-white/5">
                            Blocks: {item.blockCount}
                        </span>
                    )}
                    {hasCompactionNote && (
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 dark:bg-white/5">
                            {item.compactionApplied ? "已压缩" : "未压缩"}
                            {item.estimatedSavedTokens ? ` · 节省 ${item.estimatedSavedTokens} tokens` : ""}
                        </span>
                    )}
                    {item.durableFlushReason && (
                        <span className="rounded-full bg-stone-100 px-2.5 py-1 dark:bg-white/5">
                            durable: {item.durableFlushReason}
                        </span>
                    )}
                </div>

                {item.blockTypes.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                        {item.blockTypes.slice(0, 6).map((type) => (
                            <span
                                key={`${item.id}:${type}`}
                                className="rounded-full border border-stone-200/80 bg-white/80 px-2 py-0.5 text-[10px] text-stone-600 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-300"
                            >
                                {type}
                            </span>
                        ))}
                    </div>
                )}

                {(item.scopeChain.length > 0 || item.triggerReason || item.blockSummaryLines.length > 0) && (
                    <div className="mt-2.5 space-y-1.5 text-[12px] leading-5 text-muted-foreground">
                        {item.triggerReason && <div>触发原因：{item.triggerReason}</div>}
                        {item.scopeChain.length > 0 && <div>Scope 链：{item.scopeChain.join(" -> ")}</div>}
                        {item.blockSummaryLines.length > 0 && (
                            <ul className="space-y-1">
                                {item.blockSummaryLines.slice(0, 2).map((line) => (
                                    <li key={`${item.id}:${line}`} className="rounded-2xl bg-stone-100/80 px-3 py-2 dark:bg-white/[0.04]">
                                        {line}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="space-y-3">
            {renderGovernanceCard(latest, "latest")}
            {historyItems.length > 0 && (
                <div className="space-y-2">
                    {historyItems.map((item) => renderGovernanceCard(item, "history"))}
                </div>
            )}
        </div>
    );
}

/**
 * @deprecated Runtime 细节卡已从 Web 产品面退役。保留两个迭代仅供旧调用迁移，
 * 新入口应使用 Workbench 的阶段级运行摘要与专用审批面。
 */
export function RuntimeTimelinePanel({
    isOpen,
    onClose,
    model,
    selectedRuntimeId,
    processes,
    overallStatus,
    currentStepTitle,
    pendingApproval,
    contextGovernance,
    contextGovernanceHistory,
    onSelectRuntime,
}: RuntimeTimelinePanelProps) {
    const { locale } = useLocale();
    const runtimeId = selectedRuntimeId && model.items.some((item) => item.id === selectedRuntimeId)
        ? selectedRuntimeId
        : model.activeRuntimeId || model.items[0]?.id || null;
    const runtime = runtimeId ? getRuntimeDescriptor(runtimeId, locale) : null;
    const Icon = runtimeId ? runtimeIcons[runtimeId] : Cpu;
    const activities = React.useMemo(
        () => runtimeId
            ? compactRuntimeActivities(model.activities.filter((activity) => activity.runtimeId === runtimeId))
            : [],
        [model.activities, runtimeId],
    );
    const globalEpisodeActivities = React.useMemo(
        () => model.activities.filter(isWebRuntimeEpisodeActivity).slice(0, CHAT_RUNTIME_ACTIVITY_WINDOW),
        [model.activities],
    );
    const swarmActivities = React.useMemo(
        () => model.activities.filter((activity) => activity.runtimeId === "subagent_swarm").slice(0, CHAT_RUNTIME_ACTIVITY_WINDOW),
        [model.activities],
    );
    const isChatRuntime = runtimeId === "chat";
    const hasChatExecutionMap = globalEpisodeActivities.length > 0 || swarmActivities.length > 0;
    const isSubagentRuntime = runtimeId === "subagent_swarm";
    return (
        <AnimatePresence>
            {isOpen && model.items.length > 0 && (
                <>
                    <motion.button
                        type="button"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.18 }}
                        className="fixed inset-0 z-[90] bg-black/38 backdrop-blur-[4px]"
                        onClick={onClose}
                    />

                    <motion.div
                        initial={{ opacity: 0, y: 18, scale: 0.985 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 18, scale: 0.985 }}
                        transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
                        className={cn(
                            "fixed z-[95] overflow-hidden border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(247,244,238,0.97))] shadow-[0_30px_100px_rgba(15,23,42,0.18)] backdrop-blur-2xl dark:border-white/10 dark:bg-[linear-gradient(180deg,rgba(24,24,27,0.985),rgba(15,15,18,0.975))]",
                            "inset-x-3 bottom-3 top-[6.5vh] rounded-[24px] md:left-1/2 md:top-[46%] md:h-[min(72vh,680px)] md:w-[min(660px,calc(100vw-4rem))] md:-translate-x-1/2 md:-translate-y-1/2",
                        )}
                    >
                        <div className="flex h-full flex-col">
                            <div className="border-b border-stone-200/70 px-4 py-2.5 dark:border-white/5 sm:px-[18px]">
                                <div className="mb-2.5 flex items-start justify-between gap-3">
                                    <div className="flex items-center gap-3">
                                        <div className="flex h-9 w-9 items-center justify-center rounded-2xl border border-stone-200/80 bg-white/90 text-stone-700 shadow-sm dark:border-white/10 dark:bg-white/[0.05] dark:text-stone-100">
                                            <Icon className="h-4 w-4" />
                                        </div>
                                        <div className="min-w-0">
                                                <div className="text-[16px] font-semibold tracking-tight text-foreground">
                                                {runtime?.label || "Runtime"}
                                            </div>
                                            <div className="mt-0.5 flex min-w-0 flex-nowrap items-center gap-2 overflow-hidden text-[11px] text-muted-foreground">
                                                {overallStatus && <span>{overallStatus}</span>}
                                                {pendingApproval && <span className="text-rose-600 dark:text-rose-300">等待审批</span>}
                                                {currentStepTitle && <span className="truncate">· {currentStepTitle}</span>}
                                            </div>
                                        </div>
                                    </div>

                                    <button
                                        type="button"
                                        aria-label="关闭"
                                        onClick={onClose}
                                        className="flex h-[34px] w-[34px] shrink-0 flex-none items-center justify-center rounded-full border border-stone-200/80 bg-white/80 text-stone-600 transition-colors hover:bg-white hover:text-stone-900 dark:border-white/10 dark:bg-white/[0.05] dark:text-stone-300 dark:hover:bg-white/10 dark:hover:text-stone-50"
                                    >
                                        <X className="h-4 w-4" />
                                    </button>
                                </div>

                                <div className="scrollbar-hide flex gap-1.5 overflow-x-auto pb-0.5">
                                    {model.items.map((item) => {
                                        const TabIcon = runtimeIcons[item.id];
                                        return (
                                            <button
                                                key={item.id}
                                                type="button"
                                                onClick={() => onSelectRuntime(item.id)}
                                                className={cn(
                                                    "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-stone-200/80 bg-white/80 text-stone-500 transition-all hover:border-stone-300 hover:text-stone-900 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-400 dark:hover:border-white/20 dark:hover:text-stone-100",
                                                    item.id === runtimeId && "border-amber-300/80 bg-amber-50 text-amber-700 shadow-[0_0_20px_rgba(245,158,11,0.18)] dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200",
                                                )}
                                                title={item.label}
                                                aria-label={item.label}
                                            >
                                                <TabIcon className="h-3.5 w-3.5" />
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>

                            <div className="custom-scrollbar flex-1 overflow-y-auto px-4 py-2.5 sm:px-[18px]">
                                {runtimeId === "context_governance" && (
                                    <ContextGovernanceSection
                                        contextGovernance={contextGovernance}
                                        contextGovernanceHistory={contextGovernanceHistory}
                                    />
                                )}

                                {!isChatRuntime && !isSubagentRuntime && activities.length > 0 && (
                                    <div className={cn("hidden md:block", (contextGovernance || (contextGovernanceHistory || []).length > 0) ? "mt-4" : "")}>
                                        <BroadcastRail activities={activities.slice(0, 8)} />
                                    </div>
                                )}

                                <div className={cn("space-y-3", activities.length > 0 || (isChatRuntime && globalEpisodeActivities.length > 0) ? "md:mt-4" : "")}>
                                    {isChatRuntime ? (
                                        <>
                                            {globalEpisodeActivities.length > 0 && <RuntimeEpisodeBoard activities={globalEpisodeActivities} />}
                                            {swarmActivities.length > 0 && <SwarmNodeBoard activities={swarmActivities} />}
                                            {!hasChatExecutionMap && (
                                                <div className="rounded-[20px] border border-dashed border-stone-300/80 bg-white/80 px-4 py-5 text-center text-sm leading-6 text-muted-foreground dark:border-white/10 dark:bg-white/[0.03]">
                                                    当前还没有可展示的执行地图节点。
                                                </div>
                                            )}
                                        </>
                                    ) : isSubagentRuntime ? (
                                        <div className="rounded-[20px] border border-dashed border-stone-300/80 bg-white/80 px-4 py-5 text-center text-sm leading-6 text-muted-foreground dark:border-white/10 dark:bg-white/[0.03]">
                                            子代理蜂群已合并到对话运行的执行地图里；请在 Supervisor 气泡或对话运行入口查看调度树。
                                        </div>
                                    ) : activities.length > 0 ? (
                                        <>
                                            {activities.map((activity, index) => {
                                                const shouldGroup = runtimeId === "engineering_lane";
                                                const currentGroupId = !shouldGroup
                                                    ? ""
                                                    : getEngineeringGroupId(activity);
                                                const previousGroupId = !shouldGroup
                                                    ? ""
                                                    : getEngineeringGroupId(activities[index - 1]);
                                                const showTaskHeader = shouldGroup && currentGroupId !== previousGroupId;
                                                const taskLabel = !showTaskHeader
                                                    ? null
                                                    : getEngineeringLabel(activity);
                                                return (
                                                    <div key={activity.id} className="space-y-2.5">
                                                        {taskLabel && (
                                                            <div className="rounded-[20px] border border-stone-200/80 bg-stone-50/90 px-3.5 py-2.5 dark:border-white/10 dark:bg-white/[0.035]">
                                                                <div className="line-clamp-2 text-[13px] font-semibold leading-5 text-foreground/90">
                                                                    {taskLabel.title}
                                                                </div>
                                                                <div className="mt-1 text-[11px] text-muted-foreground">
                                                                    {taskLabel.meta}
                                                                </div>
                                                            </div>
                                                        )}
                                                        <ActivityFeedItem activity={activity} processes={processes} />
                                                    </div>
                                                );
                                            })}
                                        </>
                                    ) : (
                                        <div className="rounded-[20px] border border-dashed border-stone-300/80 bg-white/80 px-4 py-5 text-center text-sm leading-6 text-muted-foreground dark:border-white/10 dark:bg-white/[0.03]">
                                            当前还没有可展示的运行记录。
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    </motion.div>
                </>
            )}
        </AnimatePresence>
    );
}
