"use client";

import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
    type AdminProcessRef,
    type ContextGovernanceView,
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
import { ContentDispatcher } from "./ContentDispatcher";
import { Activity, AlertTriangle, Blocks, Bot, Box, Code2, Cpu, Database, GitBranch, Globe, RadioTower, Route, Shield, TerminalSquare, Workflow, X } from "lucide-react";

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
    planner_lane: Route,
    engineering_lane: Code2,
    extensions: Blocks,
    automation: Workflow,
    memory: Database,
    context_governance: Shield,
    subagent_swarm: GitBranch,
    network_supervisor: Globe,
    plugin_host_tool: RadioTower,
    plugin_host_channel: RadioTower,
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

function readString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function readRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function getSwarmTaskBriefId(activity?: RuntimeStageActivity): string {
    if (!activity) return "";
    const data = readExecutionData(activity);
    return readString(data.taskBriefId)
        || readString(data.task_brief_id)
        || readString(data.invocationId)
        || activity.id;
}

function getSwarmTaskLabel(activity: RuntimeStageActivity): { title: string; meta: string } {
    const data = readExecutionData(activity);
    const title = readString(data.taskGoal) || readString(data.task_goal) || activity.summary || "Subagent task";
    const lane = readString(data.lane) || "subagent";
    const agent = lane === "external_worker"
        ? readString(data.workerType) || readString(data.targetLabel) || readString(data.subagentName) || "External worker"
        : readString(data.subagentName) || readString(data.targetLabel) || readString(data.subagentId) || activity.actorLabel || "Subagent";
    const status = readString(data.status) || "running";
    const commandSession = readRecord(data.commandSession);
    const traceRef = readRecord(data.traceRef);
    const commandId = readString(commandSession.commandId) || readString(traceRef.commandId);
    const metaParts = [`${agent}`, status];
    if (lane === "external_worker") {
        metaParts.unshift("external");
    }
    if (commandId) {
        metaParts.push(commandId);
    }
    return {
        title,
        meta: metaParts.join(" · "),
    };
}

function getPlannerPlanId(activity?: RuntimeStageActivity): string {
    if (!activity) return "";
    const data = readExecutionData(activity);
    return readString(data.planId)
        || readString(data.plan_id)
        || readString((readRecord(data.traceRef)).planId)
        || activity.id;
}

function getPlannerPlanLabel(activity: RuntimeStageActivity): { title: string; meta: string } {
    const data = readExecutionData(activity);
    const title = readString(data.planSummary) || readString(data.summary) || activity.summary || "Planner plan";
    const executionStrategy = readString(data.executionStrategy) || "direct";
    const taskCount = Number(data.taskCount || (Array.isArray(data.taskBriefs) ? data.taskBriefs.length : 0) || 0);
    const selectedDelegations = Array.isArray(data.selectedDelegations) ? data.selectedDelegations.length : 0;
    const riskFlags = Array.isArray(data.riskFlags)
        ? data.riskFlags.map((item) => readString(item)).filter(Boolean)
        : [];
    const metaParts = [executionStrategy];
    if (taskCount > 0) metaParts.push(`${taskCount} tasks`);
    if (selectedDelegations > 0) metaParts.push(`${selectedDelegations} delegated`);
    if (riskFlags.length > 0) metaParts.push(`risks: ${riskFlags.slice(0, 2).join(", ")}`);
    return {
        title,
        meta: metaParts.join(" · "),
    };
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
    const runtimeId = selectedRuntimeId || model.activeRuntimeId || model.items[0]?.id || null;
    const runtime = runtimeId ? getRuntimeDescriptor(runtimeId) : null;
    const Icon = runtimeId ? runtimeIcons[runtimeId] : Cpu;
    const activities = runtimeId
        ? model.activities.filter((activity) => activity.runtimeId === runtimeId)
        : [];
    return (
        <AnimatePresence>
            {isOpen && (
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
                                <ContextGovernanceSection
                                    contextGovernance={contextGovernance}
                                    contextGovernanceHistory={contextGovernanceHistory}
                                />

                                {activities.length > 0 && (
                                    <div className={cn("hidden md:block", (contextGovernance || (contextGovernanceHistory || []).length > 0) ? "mt-4" : "")}>
                                        <BroadcastRail activities={activities.slice(0, 8)} />
                                    </div>
                                )}

                                <div className={cn("space-y-3", activities.length > 0 ? "md:mt-4" : "")}>
                                    {activities.length > 0 ? (
                                        activities.map((activity, index) => {
                                            const shouldGroup = runtimeId === "subagent_swarm" || runtimeId === "planner_lane" || runtimeId === "engineering_lane";
                                            const currentGroupId = !shouldGroup
                                                ? ""
                                                : runtimeId === "subagent_swarm"
                                                    ? getSwarmTaskBriefId(activity)
                                                    : runtimeId === "planner_lane"
                                                        ? getPlannerPlanId(activity)
                                                        : getEngineeringGroupId(activity);
                                            const previousGroupId = !shouldGroup
                                                ? ""
                                                : runtimeId === "subagent_swarm"
                                                    ? getSwarmTaskBriefId(activities[index - 1])
                                                    : runtimeId === "planner_lane"
                                                        ? getPlannerPlanId(activities[index - 1])
                                                        : getEngineeringGroupId(activities[index - 1]);
                                            const showTaskHeader = shouldGroup && currentGroupId !== previousGroupId;
                                            const taskLabel = !showTaskHeader
                                                ? null
                                                : runtimeId === "subagent_swarm"
                                                    ? getSwarmTaskLabel(activity)
                                                    : runtimeId === "planner_lane"
                                                        ? getPlannerPlanLabel(activity)
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
                                        })
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
