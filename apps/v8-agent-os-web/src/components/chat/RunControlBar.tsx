"use client";

import { AlertCircle, CornerDownRight, PauseCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface RunControlBarProps {
    runId?: string;
    status?: string;
    pendingApproval?: boolean;
    isBusy?: boolean;
    onInterrupt?: () => void;
    onRetry?: () => void;
    onOpenApproval?: () => void;
}

const STATUS_LABELS: Record<string, string> = {
    queued: "排队中",
    running: "执行中",
    waiting_approval: "等待审批",
    waiting_input: "等待输入",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
};

const STATUS_TONES: Record<string, { dot: string; pill: string; text: string }> = {
    queued: {
        dot: "bg-sky-500",
        pill: "bg-sky-500/10 border-sky-500/20",
        text: "text-sky-700 dark:text-sky-300",
    },
    running: {
        dot: "bg-emerald-500",
        pill: "bg-emerald-500/10 border-emerald-500/20",
        text: "text-emerald-700 dark:text-emerald-300",
    },
    waiting_approval: {
        dot: "bg-amber-500",
        pill: "bg-amber-500/10 border-amber-500/20",
        text: "text-amber-700 dark:text-amber-300",
    },
    waiting_input: {
        dot: "bg-amber-500",
        pill: "bg-amber-500/10 border-amber-500/20",
        text: "text-amber-700 dark:text-amber-300",
    },
    paused: {
        dot: "bg-stone-500",
        pill: "bg-stone-500/10 border-stone-500/20",
        text: "text-stone-700 dark:text-stone-300",
    },
    completed: {
        dot: "bg-stone-400",
        pill: "bg-stone-500/10 border-stone-500/20",
        text: "text-stone-700 dark:text-stone-300",
    },
    failed: {
        dot: "bg-rose-500",
        pill: "bg-rose-500/10 border-rose-500/20",
        text: "text-rose-700 dark:text-rose-300",
    },
    cancelled: {
        dot: "bg-rose-500",
        pill: "bg-rose-500/10 border-rose-500/20",
        text: "text-rose-700 dark:text-rose-300",
    },
};

export function RunControlBar({
    runId,
    status,
    pendingApproval,
    isBusy,
    onInterrupt,
    onRetry,
    onOpenApproval,
}: RunControlBarProps) {
    if (!runId) {
        return null;
    }

    const normalizedStatus = status || "running";
    const label = STATUS_LABELS[normalizedStatus] || normalizedStatus;
    const tone = STATUS_TONES[normalizedStatus] || STATUS_TONES.running;

    return (
        <div className="inline-flex h-[28px] shrink-0 items-center gap-1 rounded-full border border-border/60 bg-background/78 px-1 py-0.5 shadow-sm backdrop-blur-lg dark:border-white/10 sm:h-[30px] sm:px-1.5">
            <div className="flex min-w-0 shrink-0 items-center gap-1">
                <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium sm:gap-1.5 sm:px-2.5 sm:text-[11px]", tone.pill, tone.text)}>
                    <span className="relative flex h-2.5 w-2.5">
                        {normalizedStatus === "running" || normalizedStatus === "waiting_approval" ? (
                            <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", tone.dot)} />
                        ) : null}
                        <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", tone.dot)} />
                    </span>
                    {label}
                </span>
                <span className="hidden rounded-full bg-stone-900/5 px-2 py-0.5 font-mono text-[10px] text-muted-foreground dark:bg-white/5 lg:inline-block">
                    {runId.length > 18 ? `${runId.slice(0, 8)}...${runId.slice(-6)}` : runId}
                </span>
                {pendingApproval && (
                    <span className="hidden items-center gap-1 rounded-full bg-amber-500/10 px-2 py-1 text-[10px] text-amber-700 dark:text-amber-300 min-[430px]:inline-flex">
                        <AlertCircle className="h-3.5 w-3.5" />
                        待审批
                    </span>
                )}
            </div>

            <div className="flex shrink-0 items-center gap-1">
                {pendingApproval && onOpenApproval && (
                    <Button type="button" variant="outline" size="sm" onClick={onOpenApproval} className="h-6 rounded-full border-stone-200/80 bg-white/70 px-2 text-[10px] dark:border-white/10 dark:bg-white/[0.04] sm:h-[26px] sm:px-2.5 sm:text-[11px]">
                        <CornerDownRight className="h-3.5 w-3.5 sm:mr-1.5" />
                        <span className="hidden sm:inline">审批</span>
                    </Button>
                )}
                {normalizedStatus === "running" && onInterrupt && (
                    <Button type="button" variant="outline" size="sm" onClick={onInterrupt} disabled={isBusy} className="h-6 rounded-full border-stone-200/80 bg-white/70 px-2 text-[10px] dark:border-white/10 dark:bg-white/[0.04] sm:h-[26px] sm:px-2.5 sm:text-[11px]">
                        <PauseCircle className="h-3.5 w-3.5 sm:mr-1.5" />
                        <span className="hidden sm:inline">中断</span>
                    </Button>
                )}
                {["paused", "failed", "cancelled", "waiting_input"].includes(normalizedStatus) && onRetry && (
                    <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={isBusy} className="h-6 rounded-full border-stone-200/80 bg-white/70 px-2 text-[10px] dark:border-white/10 dark:bg-white/[0.04] sm:h-[26px] sm:px-2.5 sm:text-[11px]">
                        <RefreshCw className="h-3.5 w-3.5 sm:mr-1.5" />
                        <span className="hidden sm:inline">重试</span>
                    </Button>
                )}
                {!pendingApproval && normalizedStatus === "waiting_approval" && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-1 text-[10px] text-amber-700 dark:text-amber-300">
                        <AlertCircle className="h-3.5 w-3.5" />
                        等待恢复
                    </span>
                )}
            </div>
        </div>
    );
}
