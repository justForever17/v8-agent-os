/* eslint-disable @typescript-eslint/no-explicit-any */
import { ChevronDown, CheckCircle2, CircleAlert, Clock3, Loader2, ShieldAlert, Square, Workflow } from "lucide-react";
import { useState, memo } from "react";
import { cn } from "@/lib/utils";
import { motion, AnimatePresence } from "framer-motion";
import type { ClientToolSurface, ClientToolSurfaceStatus } from "@v8/session-realtime";
import { useT } from "@/components/providers/LocaleProvider";

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

function looksLikeRawStructuredOutput(value: unknown) {
    if (value === null || value === undefined) {
        return false;
    }
    if (typeof value === "string") {
        const trimmed = value.trim();
        return trimmed.startsWith("{") || trimmed.startsWith("[");
    }
    return typeof value === "object";
}

function buildReadableResult(toolInvocation: ToolInvocation, t: (key: string, values?: Record<string, string | number>) => string) {
    const result = 'result' in toolInvocation ? toolInvocation.result : null;
    const surface = toolInvocation.clientSurface;
    if (looksLikeRawStructuredOutput(result) && surface) {
        const lines = [
            surface.summary,
            surface.progress ? t("web.toolCard.progress", { value: surface.progress }) : "",
            surface.actionable ? t("web.toolCard.nextStep", { value: surface.actionable }) : "",
            surface.refIds.length ? t("web.toolCard.references", { value: surface.refIds.join(", ") }) : "",
        ].filter(Boolean);
        if (lines.length) {
            return lines.join("\n");
        }
    }
    return typeof result === 'string' ? result : JSON.stringify(result, null, 2);
}

function resolveToolStatus(toolInvocation: ToolInvocation): ClientToolSurfaceStatus {
    return toolInvocation.clientSurface?.status || (toolInvocation.state === "result" ? "completed" : "running");
}

function statusPresentation(status: ClientToolSurfaceStatus) {
    if (status === "completed") return { key: "web.toolCard.completed", tone: "teal", Icon: CheckCircle2 };
    if (status === "running") return { key: "web.toolCard.running", tone: "blue", Icon: Loader2 };
    if (status === "waiting") return { key: "web.toolCard.waiting", tone: "amber", Icon: Clock3 };
    if (status === "blocked") return { key: "web.toolCard.blocked", tone: "orange", Icon: ShieldAlert };
    if (status === "timed_out") return { key: "web.toolCard.timedOut", tone: "red", Icon: CircleAlert };
    if (status === "terminated") return { key: "web.toolCard.terminated", tone: "zinc", Icon: Square };
    if (status === "failed") return { key: "web.toolCard.failed", tone: "red", Icon: CircleAlert };
    return { key: "web.toolCard.unknown", tone: "zinc", Icon: CircleAlert };
}

function toneClasses(tone: string) {
    if (tone === "teal") return "bg-teal-500/10 border-teal-500/25 text-teal-600 dark:text-teal-400";
    if (tone === "blue") return "bg-blue-500/10 border-blue-500/25 text-blue-600 dark:text-blue-400";
    if (tone === "amber") return "bg-amber-500/10 border-amber-500/25 text-amber-700 dark:text-amber-300";
    if (tone === "orange") return "bg-orange-500/10 border-orange-500/25 text-orange-700 dark:text-orange-300";
    if (tone === "red") return "bg-red-500/10 border-red-500/25 text-red-600 dark:text-red-400";
    return "bg-zinc-500/10 border-zinc-500/25 text-zinc-600 dark:text-zinc-300";
}

export const ToolCard = memo(({ toolInvocation, hideResult }: ToolCardProps) => {
    const t = useT();
    const [isExpanded, setIsExpanded] = useState(false);
    const { toolName, state } = toolInvocation;
    const hasResult = state === 'result';
    const status = resolveToolStatus(toolInvocation);
    const presentation = statusPresentation(status);
    const isActive = status === "running";
    const isSuccessful = status === "completed";
    const StatusIcon = presentation.Icon;
    const args = 'args' in toolInvocation ? toolInvocation.args : {};
    const readableResult = buildReadableResult(toolInvocation, t);

    return (
        <div className="group relative my-0.5 w-full">
            {/* Ambient Back Glow when Active */}
            {isActive && (
                <div className="absolute inset-0 bg-blue-500/10 blur-xl rounded-lg -z-10 animate-pulse" />
            )}

            <motion.div layout className={cn(
                "w-full overflow-hidden rounded-lg border backdrop-blur-md transition-all duration-500 ease-out",
                    isExpanded
                    ? isSuccessful
                        ? "bg-white/40 dark:bg-zinc-900/40 border-teal-500/30 dark:border-teal-500/20 shadow-[0_4px_24px_-8px_rgba(20,184,166,0.3)]"
                        : isActive
                            ? "bg-white/40 dark:bg-zinc-900/40 border-blue-500/30 dark:border-blue-500/20 shadow-[0_4px_24px_-8px_rgba(59,130,246,0.3)]"
                            : "bg-white/40 dark:bg-zinc-900/40 border-foreground/20 shadow-[0_4px_24px_-8px_rgba(113,113,122,0.22)]"
                    : "bg-white/20 dark:bg-zinc-900/20 border-white/20 dark:border-white/10 hover:border-foreground/20 hover:bg-white/30 dark:hover:bg-zinc-900/30"
            )}>
                <div
                    className="relative z-10 flex w-full cursor-pointer select-none items-center justify-between px-2.5 py-1"
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <div className="flex items-center gap-2 min-w-0">
                        {/* Icon Node */}
                        <div className={cn(
                            "relative flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded border",
                            toneClasses(presentation.tone)
                        )}>
                            <Workflow className={cn("h-2.5 w-2.5", isActive && "animate-pulse")} />
                            {isActive && (
                                <span className="absolute inset-0 rounded ring-1 ring-blue-500 animate-ping opacity-30" />
                            )}
                        </div>

                        {/* Title */}
                        <span className={cn(
                            "font-mono text-[11px] font-semibold tracking-wide transition-colors truncate",
                            isExpanded ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
                        )}>
                            {toolName}
                        </span>

                        {/* Status Badge */}
                        <span className={cn(
                            "flex items-center gap-1 rounded-full border px-1.5 py-0.2 text-[9px] shadow-sm transition-colors duration-500 shrink-0",
                            toneClasses(presentation.tone)
                        )}>
                            <StatusIcon className={cn("h-2.5 w-2.5", isActive && "animate-spin")} />
                            <span>{t(presentation.key)}</span>
                        </span>
                    </div>

                    <motion.div
                        animate={{ rotate: isExpanded ? 180 : 0 }}
                        transition={{ duration: 0.3, type: "spring", stiffness: 200, damping: 20 }}
                    >
                        <ChevronDown className={cn(
                            "w-3.5 h-3.5 transition-colors",
                            isExpanded 
                                ? isSuccessful ? "text-teal-500 dark:text-teal-400" : isActive ? "text-blue-500 dark:text-blue-400" : "text-muted-foreground"
                                : "text-muted-foreground/50 group-hover:text-foreground/70"
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
                            transition={{ type: "spring", stiffness: 300, damping: 25, mass: 0.8 }}
                            className="overflow-hidden"
                        >
                            <div className="space-y-2 px-2.5 pb-2.5 pt-0.5">
                                {toolInvocation.clientSurface?.summary ? (
                                    <div className="rounded border border-border/60 bg-muted/40 px-2 py-1.5 text-[11px] leading-4 text-muted-foreground">
                                        {toolInvocation.clientSurface.summary}
                                    </div>
                                ) : null}
                                {/* Arguments */}
                                <div>
                                    <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground">{t("web.toolCard.input")}</div>
                                    <div className="overflow-x-auto rounded border border-black/5 bg-black/5 p-2 shadow-inner dark:border-white/5 dark:bg-black/20">
                                        <pre className="text-[11px] font-mono text-zinc-600 dark:text-zinc-400">{JSON.stringify(args, null, 2)}</pre>
                                    </div>
                                </div>

                                {/* Result */}
                                {hasResult && !hideResult && (
                                    <motion.div
                                        initial={{ opacity: 0, y: 10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        transition={{ delay: 0.1, type: "spring", stiffness: 300, damping: 25 }}
                                    >
                                        <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground">{t("web.toolCard.output")}</div>
                                        <div className="custom-scrollbar max-h-56 overflow-x-auto rounded border border-black/5 bg-black/5 p-2 shadow-inner dark:border-white/5 dark:bg-black/20">
                                            <pre className="text-[11px] font-mono text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-all">
                                                {readableResult}
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
}, (prev, next) => (
    prev.toolInvocation.toolCallId === next.toolInvocation.toolCallId &&
    prev.toolInvocation.state === next.toolInvocation.state &&
    prev.hideResult === next.hideResult &&
    JSON.stringify('args' in prev.toolInvocation ? prev.toolInvocation.args : {}) === JSON.stringify('args' in next.toolInvocation ? next.toolInvocation.args : {}) &&
    JSON.stringify('result' in prev.toolInvocation ? prev.toolInvocation.result : null) === JSON.stringify('result' in next.toolInvocation ? next.toolInvocation.result : null) &&
    JSON.stringify(prev.toolInvocation.clientSurface ?? null) === JSON.stringify(next.toolInvocation.clientSurface ?? null)
));

ToolCard.displayName = "ToolCard";
