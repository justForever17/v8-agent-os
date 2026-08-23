'use client';

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, Circle, Loader2, MinusCircle, ListTodo } from "lucide-react";
import { cn } from "@/lib/utils";

export interface TodoHudItem {
    id?: string | null;
    content?: string | null;
    text?: string | null;
    status?: string | null;
}

type TodosHUDProps = {
    items: TodoHudItem[];
    isStale?: boolean;
    shouldAutoHide?: boolean;
    dismissDelayMs?: number;
};

export function TodosHUD({ items, isStale = false, shouldAutoHide = false, dismissDelayMs = 2600 }: TodosHUDProps) {
    const [isCollapsed, setIsCollapsed] = useState(false);
    const [dismissedSignature, setDismissedSignature] = useState<string | null>(null);

    const todos = useMemo(
        () =>
            items
                .map((item, index) => ({
                    id: String(item.id || `todo-${index}`),
                    text: String(item.content || item.text || "").trim(),
                    status: String(item.status || "pending"),
                }))
                .filter((item) => item.text),
        [items],
    );

    const allCompleted = todos.length > 0 && todos.every((todo) => todo.status === "done" || todo.status === "skipped");
    const todosSignature = useMemo(
        () => todos.map((todo) => `${todo.id}:${todo.status}:${todo.text}`).join("|"),
        [todos],
    );
    const dismissed = dismissedSignature === todosSignature;

    useEffect(() => {
        if (!shouldAutoHide || !allCompleted || todos.length === 0) {
            return undefined;
        }
        const timer = window.setTimeout(() => {
            setDismissedSignature(todosSignature);
        }, dismissDelayMs);
        return () => window.clearTimeout(timer);
    }, [allCompleted, dismissDelayMs, shouldAutoHide, todos.length, todosSignature]);

    return (
        <AnimatePresence initial={false}>
            {todos.length > 0 && !dismissed ? (
                <motion.div
                    key="todos-hud"
                    initial={{ opacity: 0, y: 20, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95, y: 20 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                    className="pointer-events-auto w-[min(19rem,calc(100vw-1.5rem))] max-w-full select-none"
                    layout
                >
                <div className="flex flex-col overflow-hidden rounded-2xl border border-white/30 bg-background/46 shadow-[0_18px_48px_rgba(15,23,42,0.12)] backdrop-blur-2xl dark:border-white/10 dark:bg-stone-950/42">
                    <div
                        className="flex min-h-[36px] cursor-pointer items-center gap-2 border-b border-white/15 bg-gradient-to-r from-primary/10 via-primary/5 to-transparent px-3 py-1.5 transition-colors hover:bg-primary/5 sm:min-h-[40px] sm:px-4 sm:py-2"
                        onClick={() => setIsCollapsed((current) => !current)}
                    >
                        <div className="rounded-md bg-primary/18 p-1.5 text-primary backdrop-blur-sm">
                            <ListTodo className="h-4 w-4" />
                        </div>
                        <span className="text-sm font-semibold tracking-tight text-foreground/90">Task Progress</span>
                        {isStale ? (
                            <span className="rounded-full bg-amber-500/12 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-300">
                                stale
                            </span>
                        ) : null}
                        <span className="ml-auto rounded-full bg-white/35 px-2 py-0.5 text-xs font-mono text-muted-foreground dark:bg-white/10">
                            {todos.filter((todo) => todo.status === "done").length}/{todos.length}
                        </span>
                        <motion.div animate={{ rotate: isCollapsed ? -90 : 0 }} className="ml-1 text-muted-foreground/70">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
                        </motion.div>
                    </div>

                    <AnimatePresence initial={false}>
                        {!isCollapsed ? (
                            <motion.div
                                initial={{ height: 0, opacity: 0 }}
                                animate={{ height: "auto", opacity: 1 }}
                                exit={{ height: 0, opacity: 0 }}
                                transition={{ duration: 0.2 }}
                                className="custom-scrollbar max-h-[132px] space-y-1.5 overflow-y-auto p-2 sm:max-h-[208px] sm:p-2.5"
                            >
                                {todos.map((todo) => {
                                    const isDone = todo.status === "done";
                                    const isProgress = todo.status === "in_progress";
                                    const isSkipped = todo.status === "skipped";

                                    return (
                                        <motion.div
                                            key={todo.id}
                                            layout
                                            initial={{ opacity: 0, x: -10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            className={cn(
                                                "flex items-start gap-3 rounded-xl border p-2 transition-colors duration-300",
                                                isProgress
                                                    ? "border-primary/20 bg-primary/10 shadow-inner shadow-primary/10"
                                                    : "border-white/10 bg-white/8 hover:bg-white/14 dark:border-white/5 dark:bg-white/[0.03] dark:hover:bg-white/[0.05]",
                                            )}
                                        >
                                            <div className="mt-0.5 flex shrink-0 items-center justify-center">
                                                {isDone ? (
                                                    <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 300, damping: 20 }}>
                                                        <CheckCircle2 className="h-4 w-4 rounded-full bg-background text-green-500 shadow-sm" />
                                                    </motion.div>
                                                ) : isProgress ? (
                                                    <div className="relative flex h-4 w-4 items-center justify-center">
                                                        <Loader2 className="absolute h-4 w-4 animate-spin text-primary" />
                                                        <div className="absolute h-2 w-2 animate-pulse rounded-full bg-primary/20" />
                                                    </div>
                                                ) : isSkipped ? (
                                                    <MinusCircle className="h-4 w-4 text-muted-foreground/70" />
                                                ) : (
                                                    <Circle className="h-4 w-4 text-muted-foreground/70" />
                                                )}
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <div
                                                    className={cn(
                                                        "text-sm leading-snug text-foreground/90",
                                                        (isDone || isSkipped) && "text-muted-foreground line-through",
                                                        allCompleted && "opacity-80",
                                                    )}
                                                >
                                                    {todo.text}
                                                </div>
                                            </div>
                                        </motion.div>
                                    );
                                })}
                            </motion.div>
                        ) : null}
                    </AnimatePresence>
                </div>
                </motion.div>
            ) : null}
        </AnimatePresence>
    );
}
