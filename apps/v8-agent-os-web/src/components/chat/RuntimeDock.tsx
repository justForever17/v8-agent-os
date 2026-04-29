"use client";

import { motion } from "framer-motion";
import type { ElementType } from "react";
import { cn } from "@/lib/utils";
import { RuntimeId, RuntimeStageModel } from "@/lib/runtime-stage";
import { Blocks, Bot, Code2, Cpu, Database, GitBranch, Globe, RadioTower, Route, Shield, Sparkles, TerminalSquare, Workflow } from "lucide-react";

interface RuntimeDockProps {
    model: RuntimeStageModel;
    selectedRuntimeId: RuntimeId | null;
    isPanelOpen: boolean;
    onSelectRuntime: (runtimeId: RuntimeId) => void;
}

const runtimeIcons: Record<RuntimeId, ElementType<{ className?: string }>> = {
    chat: Bot,
    planner_lane: Route,
    engineering_lane: Code2,
    extensions: Blocks,
    creative_media: Sparkles,
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

const statusStyles = {
    idle: {
        wrapper: "border-stone-200/80 bg-white/70 text-stone-500 dark:border-white/10 dark:bg-white/[0.04] dark:text-stone-400",
        glow: "",
        dot: "bg-stone-300 dark:bg-stone-600",
    },
    recent: {
        wrapper: "border-stone-300/80 bg-white/85 text-stone-700 dark:border-white/15 dark:bg-white/[0.06] dark:text-stone-200",
        glow: "",
        dot: "bg-stone-500 dark:bg-stone-300",
    },
    active: {
        wrapper: "border-amber-300/80 bg-amber-50/90 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200",
        glow: "shadow-[0_0_28px_rgba(245,158,11,0.28)]",
        dot: "bg-amber-500",
    },
    attention: {
        wrapper: "border-rose-300/80 bg-rose-50/90 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200",
        glow: "shadow-[0_0_28px_rgba(244,63,94,0.24)]",
        dot: "bg-rose-500",
    },
} as const;

function RuntimeIconButton({
    runtimeId,
    label,
    status,
    selected,
    eventCount,
    onClick,
}: {
    runtimeId: RuntimeId;
    label: string;
    status: "idle" | "recent" | "active" | "attention";
    selected: boolean;
    eventCount: number;
    onClick: () => void;
}) {
    const Icon = runtimeIcons[runtimeId];
    const tone = statusStyles[status];
    const shouldPulse = status === "active" || status === "attention";

    return (
        <button
            type="button"
            aria-label={label}
            title={label}
            onClick={onClick}
            className={cn(
                "group relative flex h-[28px] w-[28px] items-center justify-center rounded-xl border transition-all duration-200 sm:h-[30px] sm:w-[30px]",
                "hover:-translate-y-0.5 hover:scale-[1.03] hover:border-stone-300 dark:hover:border-white/20",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                tone.wrapper,
                tone.glow,
                selected && "ring-1 ring-amber-400/60 ring-offset-2 ring-offset-background",
            )}
        >
            {shouldPulse && (
                <motion.span
                    className={cn("absolute inset-0 rounded-xl", status === "attention" ? "bg-rose-500/10" : "bg-amber-500/10")}
                    animate={{ opacity: [0.2, 0.7, 0.2], scale: [0.96, 1.04, 0.96] }}
                    transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
                />
            )}

            <motion.span
                className="relative z-10"
                animate={status === "attention" ? { rotate: [0, -4, 4, -3, 3, 0] } : { rotate: 0 }}
                transition={status === "attention" ? { duration: 0.42, repeat: Infinity, repeatDelay: 2.6, ease: "easeInOut" } : undefined}
            >
                <Icon className="h-[11px] w-[11px] sm:h-[13px] sm:w-[13px]" />
            </motion.span>

            <span className={cn("absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full border border-background sm:h-2 sm:w-2", tone.dot)} />

            {eventCount > 0 && (
                <span className="absolute -bottom-1 -right-1 min-w-[11px] rounded-full border border-background bg-stone-900 px-1 py-0.5 text-[7px] font-semibold leading-none text-white dark:bg-stone-100 dark:text-stone-900 sm:min-w-[13px] sm:text-[8px]">
                    {Math.min(eventCount, 9)}
                </span>
            )}
        </button>
    );
}

export function RuntimeDock({ model, selectedRuntimeId, isPanelOpen, onSelectRuntime }: RuntimeDockProps) {
    return (
        <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            className={cn(
                "inline-flex shrink-0 items-center gap-0.5 rounded-full border border-stone-200/80 bg-white/76 p-[3px] shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur-xl dark:border-white/10 dark:bg-stone-950/75",
                isPanelOpen && "border-amber-300/70 dark:border-amber-500/20",
            )}
        >
            {model.items.map((item) => (
                <RuntimeIconButton
                    key={item.id}
                    runtimeId={item.id}
                    label={item.label}
                    status={item.status}
                    selected={selectedRuntimeId === item.id && isPanelOpen}
                    eventCount={item.eventCount}
                    onClick={() => onSelectRuntime(item.id)}
                />
            ))}
        </motion.div>
    );
}
