"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, Circle, Loader2, MinusCircle, ListTodo } from "lucide-react";
import { cn } from "@/lib/utils";

interface TodoItem {
    id: string;
    text: string;
    status: "pending" | "in_progress" | "done" | "skipped";
}

interface TodoTaskSnapshot {
    taskId: string;
    taskName: string;
    sessionId?: string | null;
    runId?: string | null;
    updatedAt?: string | null;
    isActive?: boolean;
    isStale?: boolean;
    allCompleted?: boolean;
    items: TodoItem[];
}

interface TodoApiItem {
    id?: unknown;
    text?: unknown;
    status?: unknown;
}

interface TodoApiPayload {
    taskId?: unknown;
    taskName?: unknown;
    task_name?: unknown;
    sessionId?: unknown;
    runId?: unknown;
    updatedAt?: unknown;
    isActive?: unknown;
    isStale?: unknown;
    allCompleted?: unknown;
    items?: TodoApiItem[];
}

function asOptionalString(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value : null;
}

function useTodosState(sessionId?: string | null) {
    const [snapshot, setSnapshot] = useState<TodoTaskSnapshot | null>(null);
    const lastErrorSignatureRef = useRef<string>("");
    const activeControllerRef = useRef<AbortController | null>(null);
    const isFetchingRef = useRef(false);

    useEffect(() => {
        if (!sessionId) {
            activeControllerRef.current?.abort();
            activeControllerRef.current = null;
            isFetchingRef.current = false;
            setSnapshot(null);
            return;
        }

        let intervalId: number | null = null;
        let isDisposed = false;

        const load = async () => {
            if (isFetchingRef.current || isDisposed) {
                return;
            }
            if (typeof document !== "undefined" && document.visibilityState === "hidden") {
                return;
            }
            const controller = new AbortController();
            activeControllerRef.current = controller;
            isFetchingRef.current = true;
            try {
                const response = await fetch(`/api/sessions/${sessionId}/todos`, {
                    cache: "no-store",
                    signal: controller.signal,
                });
                if (!response.ok) {
                    if (response.status === 401 || response.status === 404) {
                        if (!isDisposed) {
                            setSnapshot(null);
                        }
                        return;
                    }
                    const payload = await response.json().catch(() => ({}));
                    const signature = `${response.status}:${String(payload?.detail || payload?.error || "unknown")}`;
                    if (signature !== lastErrorSignatureRef.current) {
                        lastErrorSignatureRef.current = signature;
                        console.warn("Failed to load todos snapshot", payload?.detail || payload?.error || response.statusText);
                    }
                    return;
                }
                lastErrorSignatureRef.current = "";
                const payload = await response.json();
                const todo = payload?.todo as TodoApiPayload | undefined;
                if (!isDisposed && todo && Array.isArray(todo.items)) {
                    setSnapshot({
                        taskId: String(todo.taskId || ""),
                        taskName: String(todo.taskName || todo.task_name || "task"),
                        sessionId: asOptionalString(todo.sessionId),
                        runId: asOptionalString(todo.runId),
                        updatedAt: asOptionalString(todo.updatedAt),
                        isActive: Boolean(todo.isActive),
                        isStale: Boolean(todo.isStale),
                        allCompleted: Boolean(todo.allCompleted),
                        items: todo.items.map((item: TodoApiItem, index: number) => ({
                            id: String(item.id || `${todo.taskId || "task"}-item-${index}`),
                            text: String(item.text || "").trim(),
                            status: (["pending", "in_progress", "done", "skipped"].includes(String(item.status)) ? String(item.status) : "pending") as TodoItem["status"],
                        })),
                    });
                } else {
                    if (!isDisposed) {
                        setSnapshot(null);
                    }
                }
            } catch (error) {
                if (!isDisposed && (error as Error).name !== "AbortError") {
                    console.warn("Failed to load todos snapshot", error);
                }
            } finally {
                if (activeControllerRef.current === controller) {
                    activeControllerRef.current = null;
                }
                isFetchingRef.current = false;
            }
        };
        void load();
        if (typeof window !== "undefined") {
            intervalId = window.setInterval(() => {
                void load();
            }, 4000);
        }

        const handleVisibilityRefresh = () => {
            if (typeof document === "undefined" || document.visibilityState === "visible") {
                void load();
            }
        };

        if (typeof document !== "undefined") {
            document.addEventListener("visibilitychange", handleVisibilityRefresh);
        }
        if (typeof window !== "undefined") {
            window.addEventListener("focus", handleVisibilityRefresh);
        }

        return () => {
            isDisposed = true;
            activeControllerRef.current?.abort();
            activeControllerRef.current = null;
            isFetchingRef.current = false;
            if (intervalId !== null && typeof window !== "undefined") {
                window.clearInterval(intervalId);
            }
            if (typeof document !== "undefined") {
                document.removeEventListener("visibilitychange", handleVisibilityRefresh);
            }
            if (typeof window !== "undefined") {
                window.removeEventListener("focus", handleVisibilityRefresh);
            }
        };
    }, [sessionId]);

    const effective = useMemo(() => {
        if (!snapshot?.items?.length) {
            return null;
        }
        return snapshot;
    }, [snapshot]);
    const todos = effective?.items || [];
    const allCompleted = effective?.allCompleted ?? (todos.length > 0 && todos.every((todo) => todo.status === "done" || todo.status === "skipped"));

    return {
        task: effective,
        todos,
        isActive: Boolean(effective?.isActive ?? (todos.length > 0 && !allCompleted)),
        allCompleted,
    };
}

export function TodosHUD({ sessionId }: { sessionId?: string | null }) {
    const { task, todos } = useTodosState(sessionId);
    const [isCollapsed, setIsCollapsed] = useState(false);
    const show = todos.length > 0;

    return (
        <AnimatePresence>
            {show && (
                <motion.div
                    initial={{ opacity: 0, y: 20, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95, y: 20 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                    className="pointer-events-auto w-[min(19rem,calc(100vw-1.5rem))] max-w-full select-none"
                    layout
                >
                    <div className="flex flex-col overflow-hidden rounded-2xl border border-white/30 bg-background/46 shadow-[0_18px_48px_rgba(15,23,42,0.12)] backdrop-blur-2xl dark:border-white/10 dark:bg-stone-950/42">
                        <div
                            className="flex min-h-[36px] cursor-pointer items-center gap-2 border-b border-white/15 bg-gradient-to-r from-primary/10 via-primary/5 to-transparent px-3 py-1.5 sm:min-h-[40px] sm:px-4 sm:py-2 transition-colors hover:bg-primary/5"
                            onClick={() => setIsCollapsed(!isCollapsed)}
                        >
                            <div className="rounded-md bg-primary/18 p-1.5 text-primary backdrop-blur-sm">
                                <ListTodo className="h-4 w-4" />
                            </div>
                            <span className="text-sm font-semibold tracking-tight text-foreground/90">Task Progress</span>
                            {task?.isStale ? (
                                <span className="rounded-full bg-amber-500/12 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-300">
                                    stale
                                </span>
                            ) : null}
                            <span className="ml-auto rounded-full bg-white/35 px-2 py-0.5 text-xs font-mono text-muted-foreground dark:bg-white/10">
                                {todos.filter((todo) => todo.status === "done").length}/{todos.length}
                            </span>
                            <motion.div animate={{ rotate: isCollapsed ? -90 : 0 }} className="ml-1 text-muted-foreground/70">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                            </motion.div>
                        </div>

                        <AnimatePresence initial={false}>
                            {!isCollapsed && (
                                <motion.div
                                    initial={{ height: 0, opacity: 0 }}
                                    animate={{ height: "auto", opacity: 1 }}
                                    exit={{ height: 0, opacity: 0 }}
                                    transition={{ duration: 0.2 }}
                                    className="custom-scrollbar max-h-[132px] sm:max-h-[208px] space-y-1.5 overflow-y-auto p-2 sm:p-2.5"
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
                                                        <MinusCircle className="h-4 w-4 text-muted-foreground/60" />
                                                    ) : (
                                                        <Circle className="h-4 w-4 text-muted-foreground/40" />
                                                    )}
                                                </div>

                                                <div className="min-w-0 flex-1">
                                                    <span
                                                        className={cn(
                                                            "break-words text-xs leading-snug transition-all duration-500",
                                                            isDone
                                                                ? "text-muted-foreground line-through opacity-70"
                                                                : isProgress
                                                                  ? "font-medium text-foreground"
                                                                  : isSkipped
                                                                    ? "text-muted-foreground/50 line-through"
                                                                    : "text-muted-foreground/80",
                                                        )}
                                                    >
                                                        {todo.text}
                                                    </span>
                                                </div>
                                            </motion.div>
                                        );
                                    })}
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
