/* eslint-disable @typescript-eslint/no-explicit-any */
import { ChevronDown, CheckCircle2, Loader2, Workflow } from "lucide-react";
import { useState, memo } from "react";
import { cn } from "@/lib/utils";
import { motion, AnimatePresence } from "framer-motion";
import type { ClientToolSurface } from "@v8/session-realtime";

export interface ToolInvocation {
    toolCallId: string;
    toolName: string;
    args: any;
    state: 'call' | 'result';
    result?: any;
    clientSurface?: ClientToolSurface;
}

interface ToolCardProps {
    toolInvocation: ToolInvocation;
    hideResult?: boolean;
}

export const ToolCard = memo(({ toolInvocation, hideResult }: ToolCardProps) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const { toolName, state } = toolInvocation;
    const isComplete = state === 'result';
    const result = 'result' in toolInvocation ? toolInvocation.result : null;
    const args = 'args' in toolInvocation ? toolInvocation.args : {};

    return (
        <div className="group relative my-1 w-full">
            {/* Ambient Back Glow when Active */}
            {!isComplete && (
                <div className="absolute inset-0 bg-blue-500/10 blur-xl rounded-xl -z-10 animate-pulse" />
            )}

            <motion.div layout className={cn(
                "w-full overflow-hidden rounded-xl border backdrop-blur-md transition-all duration-500 ease-out",
                isExpanded 
                    ? isComplete 
                        ? "bg-white/40 dark:bg-zinc-900/40 border-teal-500/30 dark:border-teal-500/20 shadow-[0_4px_24px_-8px_rgba(20,184,166,0.3)]"
                        : "bg-white/40 dark:bg-zinc-900/40 border-blue-500/30 dark:border-blue-500/20 shadow-[0_4px_24px_-8px_rgba(59,130,246,0.3)]"
                    : "bg-white/20 dark:bg-zinc-900/20 border-white/20 dark:border-white/10 hover:border-foreground/20 hover:bg-white/30 dark:hover:bg-zinc-900/30"
            )}>
                <div
                    className="relative z-10 flex w-full cursor-pointer select-none items-center justify-between px-3.5 py-1.5"
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <div className="flex items-center gap-3">
                        {/* Icon Node */}
                        <div className={cn(
                            "relative flex h-[22px] w-[22px] items-center justify-center rounded-md border",
                            isComplete 
                                ? "bg-teal-500/10 border-teal-500/30 text-teal-600 dark:text-teal-400"
                                : "bg-blue-500/20 border-blue-500/50 text-blue-600 dark:text-blue-400"
                        )}>
                            <Workflow className={cn("h-3 w-3", !isComplete && "animate-pulse")} />
                            {!isComplete && (
                                <span className="absolute inset-0 rounded-md ring-1 ring-blue-500 animate-ping opacity-30" />
                            )}
                        </div>

                        {/* Title */}
                        <span className={cn(
                            "font-mono text-[11px] font-semibold tracking-wide transition-colors",
                            isExpanded ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
                        )}>
                            {toolName}
                        </span>

                        {/* Status Badge */}
                        <span className={cn(
                            "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] shadow-sm transition-colors duration-500",
                            isComplete
                                ? "bg-teal-500/10 border-teal-500/20 text-teal-600 dark:text-teal-400"
                                : "bg-blue-500/10 border-blue-500/20 text-blue-600 dark:text-blue-400"
                        )}>
                            {isComplete ? (
                                <>
                                    <CheckCircle2 className="h-3 w-3" />
                                    <span>已完成</span>
                                </>
                            ) : (
                                <>
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                    <span>执行中</span>
                                </>
                            )}
                        </span>
                    </div>

                    <motion.div
                        animate={{ rotate: isExpanded ? 180 : 0 }}
                        transition={{ duration: 0.3, type: "spring", stiffness: 200, damping: 20 }}
                    >
                        <ChevronDown className={cn(
                            "w-4 h-4 transition-colors",
                            isExpanded 
                                ? isComplete ? "text-teal-500 dark:text-teal-400" : "text-blue-500 dark:text-blue-400" 
                                : "text-muted-foreground/50 group-hover:text-foreground/70"
                        )} />
                    </motion.div>
                </div>

                {toolInvocation.clientSurface?.summary ? (
                    <div className="px-3.5 pb-1.5 text-[11px] leading-4 text-muted-foreground">
                        {toolInvocation.clientSurface.summary}
                    </div>
                ) : null}

                {/* Expanded Content with Framer Motion AnimatePresence */}
                <AnimatePresence initial={false}>
                    {isExpanded && (
                        <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: "auto", opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ type: "spring", stiffness: 300, damping: 25, mass: 0.8 }}
                            className="overflow-hidden"
                        >
                            <div className="space-y-2.5 px-3.5 pb-3.5 pt-0.5">
                                {/* Arguments */}
                                <div>
                                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">输入</div>
                                    <div className="overflow-x-auto rounded-lg border border-black/5 bg-black/5 p-2.5 shadow-inner dark:border-white/5 dark:bg-black/20">
                                        <pre className="text-[11px] font-mono text-zinc-600 dark:text-zinc-400">{JSON.stringify(args, null, 2)}</pre>
                                    </div>
                                </div>

                                {/* Result */}
                                {isComplete && !hideResult && (
                                    <motion.div
                                        initial={{ opacity: 0, y: 10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{ delay: 0.1, type: "spring", stiffness: 300, damping: 25 }}
                                    >
                                        <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Agent 可见输出</div>
                                        <div className="custom-scrollbar max-h-56 overflow-x-auto rounded-lg border border-black/5 bg-black/5 p-2.5 shadow-inner dark:border-white/5 dark:bg-black/20">
                                            <pre className="text-[11px] font-mono text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-all">
                                                {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
                                            </pre>
                                        </div>
                                    </motion.div>
                                )}
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </motion.div>
        </div>
    );
}, (prev, next) => {
    // Custom comparison for toolInvocation
    return (
        prev.toolInvocation.toolCallId === next.toolInvocation.toolCallId &&
        prev.toolInvocation.state === next.toolInvocation.state &&
        prev.hideResult === next.hideResult &&
        JSON.stringify('args' in prev.toolInvocation ? prev.toolInvocation.args : {}) === JSON.stringify('args' in next.toolInvocation ? next.toolInvocation.args : {}) &&
        JSON.stringify('result' in prev.toolInvocation ? prev.toolInvocation.result : null) === JSON.stringify('result' in next.toolInvocation ? next.toolInvocation.result : null) &&
        JSON.stringify(prev.toolInvocation.clientSurface ?? null) === JSON.stringify(next.toolInvocation.clientSurface ?? null)
    );
});

ToolCard.displayName = "ToolCard";
