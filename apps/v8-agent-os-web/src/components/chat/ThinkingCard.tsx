import { ChevronDown, Atom } from "lucide-react";
import { useState, memo, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { motion, AnimatePresence } from "framer-motion";

interface ThinkingCardProps {
    content: string;
    isStreaming?: boolean;
    elapsedTime?: number;
    reasoningKind?: string;
    reasoningSurface?: Record<string, unknown>;
    data?: {
        startTime?: number;
        endTime?: number;
    };
}

export const ThinkingCard = memo(({
    content,
    isStreaming = false,
    elapsedTime,
    reasoningKind,
    reasoningSurface,
    data
}: ThinkingCardProps) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [currentElapsedTime, setCurrentElapsedTime] = useState(elapsedTime || 0);
    const hasAutoExpanded = useRef(false);
    const timerRef = useRef<NodeJS.Timeout | null>(null);

    // Auto-expand when streaming starts
    useEffect(() => {
        if (isStreaming && !hasAutoExpanded.current) {
            setTimeout(() => {
                setIsExpanded(true);
                hasAutoExpanded.current = true;
            }, 0);
        }
    }, [isStreaming]);

    // Auto-collapse when streaming ends
    useEffect(() => {
        if (!isStreaming && hasAutoExpanded.current) {
            const timer = setTimeout(() => {
                setIsExpanded(false);
                hasAutoExpanded.current = false;
            }, 800);
            return () => clearTimeout(timer);
        }
    }, [isStreaming]);

    // Real-time timer when streaming
    useEffect(() => {
        if (isStreaming && data?.startTime) {
            timerRef.current = setInterval(() => {
                const elapsed = Date.now() - (data.startTime || 0);
                setCurrentElapsedTime(elapsed);
            }, 100);

            return () => {
                if (timerRef.current) {
                    clearInterval(timerRef.current);
                    timerRef.current = null;
                }
            };
        }
    }, [isStreaming, data?.startTime]);

    // Sync elapsed time when not streaming
    useEffect(() => {
        if (!isStreaming && elapsedTime !== undefined) {
            const timer = setTimeout(() => {
                setCurrentElapsedTime(elapsedTime);
            }, 0);
            return () => clearTimeout(timer);
        }
    }, [isStreaming, elapsedTime]);

    if (!content) return null;

    const formatTime = (ms: number) => {
        if (ms < 1000) return `${ms}ms`;
        return `${(ms / 1000).toFixed(1)}s`;
    };
    const normalizedReasoningKind = String(reasoningKind || "").trim().toLowerCase();
    const title = normalizedReasoningKind.includes("summary") ? "推理摘要" : "reasoning";
    const isUnverified = Boolean(reasoningSurface?.unverified)
        || String(reasoningSurface?.trust || "").trim().toLowerCase() === "unverified";
    const shouldFadeContent = content.length > 900;

    return (
        <div className="group relative my-1 w-full">
            {/* Ambient Back Glow when Active */}
            {isStreaming && (
                <div className="absolute inset-0 bg-violet-500/10 blur-xl rounded-xl -z-10 animate-pulse" />
            )}

            <div className={cn(
                "w-full overflow-hidden rounded-xl border backdrop-blur-md transition-all duration-500 ease-out",
                isExpanded 
                    ? "bg-white/40 dark:bg-zinc-900/40 border-violet-500/30 dark:border-violet-500/20 shadow-[0_4px_24px_-8px_rgba(139,92,246,0.3)]" 
                    : "bg-white/20 dark:bg-zinc-900/20 border-white/20 dark:border-white/10 hover:border-violet-500/30 hover:bg-white/30 dark:hover:bg-zinc-900/30"
            )}>
                {/* Header (Clickable Area) */}
                <div
                    className="relative z-10 flex w-full cursor-pointer select-none items-center justify-between px-3.5 py-1.5"
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <div className="flex items-center gap-3">
                        {/* Icon Node */}
                        <div className={cn(
                            "relative flex h-[22px] w-[22px] items-center justify-center rounded-md border",
                            isStreaming 
                                ? "bg-violet-500/20 border-violet-500/50 text-violet-600 dark:text-violet-400" 
                                : "bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-zinc-500"
                        )}>
                            <Atom className={cn("h-3 w-3", isStreaming && "animate-pulse")} />
                            {isStreaming && (
                                <span className="absolute inset-0 rounded-md ring-1 ring-violet-500 animate-ping opacity-30" />
                            )}
                        </div>

                        {/* Title and Time */}
                        <span className={cn(
                            "text-[11px] font-semibold tracking-wide transition-colors",
                            isExpanded
                                ? "text-foreground"
                                : isUnverified
                                    ? "text-rose-600/70 dark:text-rose-300/70"
                                    : "text-muted-foreground group-hover:text-foreground"
                        )}>
                            {title}
                        </span>

                        {isStreaming ? (
                            <span className="text-violet-500 dark:text-violet-400 font-mono text-[10px] tabular-nums opacity-90">
                                {formatTime(currentElapsedTime)}
                            </span>
                        ) : currentElapsedTime > 0 ? (
                            <span className="text-muted-foreground/50 font-mono text-[10px] tabular-nums">
                                {formatTime(currentElapsedTime)}
                            </span>
                        ) : null}
                    </div>

                    <motion.div
                        animate={{ rotate: isExpanded ? 180 : 0 }}
                        transition={{ duration: 0.3, ease: "circOut" }}
                    >
                        <ChevronDown className={cn(
                            "w-4 h-4 transition-colors",
                            isExpanded ? "text-violet-500 dark:text-violet-400" : "text-muted-foreground/50 group-hover:text-foreground/70"
                        )} />
                    </motion.div>
                </div>

                {/* Expanded Content with Framer Motion AnimatePresence */}
                <AnimatePresence initial={false}>
                    {isExpanded && (
                        <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: "auto", opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                        >
                            <div className="px-3.5 pb-3.5 pt-0.5">
                                <div className={cn(
                                    "relative rounded-lg border border-black/5 bg-black/5 p-2.5 shadow-inner dark:border-white/5 dark:bg-black/20",
                                    shouldFadeContent && "max-h-40 overflow-hidden"
                                )}>
                                    <div className="whitespace-pre-wrap text-[11px] leading-relaxed text-zinc-600 selection:bg-violet-500/30 dark:text-zinc-400 font-mono">
                                        {content}
                                        {isStreaming && (
                                            <span className="ml-1 inline-block h-3 w-1.5 animate-pulse rounded-sm bg-violet-500 align-middle shadow-[0_0_8px_rgba(139,92,246,0.6)] dark:bg-violet-400" />
                                        )}
                                    </div>
                                    {shouldFadeContent && (
                                        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white/95 to-transparent dark:from-zinc-950/95" />
                                    )}
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
});

ThinkingCard.displayName = "ThinkingCard";
