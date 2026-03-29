"use client";

import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { cn } from "@/lib/utils";
import {
    RuntimeId,
    RuntimeStageActivity,
    RuntimeStageModel,
    formatRelativeRuntimeTime,
    getRuntimeDescriptor,
} from "@/lib/runtime-stage";
import { ContentDispatcher } from "./ContentDispatcher";
import { Activity, AlertTriangle, Blocks, Bot, Box, Cpu, Database, RadioTower, TerminalSquare, Workflow, X } from "lucide-react";

interface RuntimeTimelinePanelProps {
    isOpen: boolean;
    onClose: () => void;
    model: RuntimeStageModel;
    selectedRuntimeId: RuntimeId | null;
    overallStatus?: string;
    currentStepTitle?: string | null;
    pendingApproval?: boolean;
    onSelectRuntime: (runtimeId: RuntimeId) => void;
}

const runtimeIcons: Record<RuntimeId, React.ElementType<{ className?: string }>> = {
    chat: Bot,
    extensions: Blocks,
    automation: Workflow,
    memory: Database,
    plugin_host: RadioTower,
    computer_use: TerminalSquare,
    rpa: Cpu,
};

const kindMeta: Record<RuntimeStageActivity["kind"], { label: string; icon: React.ElementType<{ className?: string }>; tone: string }> = {
    progress: { label: "运行", icon: Activity, tone: "text-emerald-600 dark:text-emerald-300" },
    tool: { label: "工具", icon: TerminalSquare, tone: "text-sky-600 dark:text-sky-300" },
    governance: { label: "控制", icon: AlertTriangle, tone: "text-rose-600 dark:text-rose-300" },
    artifact: { label: "产物", icon: Box, tone: "text-violet-600 dark:text-violet-300" },
    handoff: { label: "交接", icon: Workflow, tone: "text-amber-600 dark:text-amber-300" },
};

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

function ActivityFeedItem({ activity }: { activity: RuntimeStageActivity }) {
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

            <div className="mb-2.5 text-[13px] font-medium leading-6 text-foreground/90">
                {activity.summary}
            </div>

            <div className="rounded-[18px] border border-stone-200/70 bg-stone-50/85 p-2.5 dark:border-white/5 dark:bg-white/[0.025]">
                {activity.node.kind === "artifact" ? (
                    <div className="text-sm text-muted-foreground">产物已记录，可在 Artifacts 面板查看详细内容。</div>
                ) : (
                    <ContentDispatcher node={activity.node} isExecuting={false} isStreaming={false} />
                )}
            </div>
        </div>
    );
}

export function RuntimeTimelinePanel({
    isOpen,
    onClose,
    model,
    selectedRuntimeId,
    overallStatus,
    currentStepTitle,
    pendingApproval,
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
                                {activities.length > 0 && (
                                    <div className="hidden md:block">
                                        <BroadcastRail activities={activities.slice(0, 8)} />
                                    </div>
                                )}

                                <div className={cn("space-y-3", activities.length > 0 ? "md:mt-4" : "")}>
                                    {activities.length > 0 ? (
                                        activities.slice(0, 24).map((activity) => (
                                            <ActivityFeedItem key={activity.id} activity={activity} />
                                        ))
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
