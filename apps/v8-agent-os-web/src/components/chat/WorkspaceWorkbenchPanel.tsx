"use client";

import React, { useMemo, useState } from "react";
import {
    Box,
    ChevronDown,
    Code2,
    FileText,
    GitCompareArrows,
    ListTodo,
    Maximize2,
    Route,
    TerminalSquare,
    X,
} from "lucide-react";
import type { AdminProcessRef } from "@v8/session-realtime";

import { cn } from "@/lib/utils";
import type { RuntimeId, RuntimeStageModel } from "@/lib/runtime-stage";
import { formatRelativeRuntimeTime } from "@/lib/runtime-stage";
import type { Message, UiExecutionNode } from "@/store/chat-types";
import { useChatStore } from "@/store/chat-store";
import { inferArtifactCardType, type RuntimeArtifact } from "@/lib/artifacts";
import { InteractiveTerminalCard } from "./InteractiveTerminalCard";
import type { TodoHudItem } from "./TodosHUD";

export type WorkbenchTab = "terminal" | "artifacts" | "todos" | "diff" | "runtime";

type WorkbenchArtifact = RuntimeArtifact & {
    sourceMessageId?: string;
};

type DiffHint = {
    id: string;
    title: string;
    subtitle: string;
    tone: "write" | "command" | "artifact";
};

interface WorkspaceWorkbenchPanelProps {
    open: boolean;
    activeTab: WorkbenchTab;
    onOpenChange: (open: boolean) => void;
    onTabChange: (tab: WorkbenchTab) => void;
    workspacePath?: string | null;
    messages: Message[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
    selectedRuntimeId: RuntimeId | null;
    onSelectRuntime: (runtimeId: RuntimeId) => void;
    onOpenRuntimeDetail: () => void;
}

const tabMeta: Record<WorkbenchTab, {
    label: string;
    icon: React.ElementType<{ className?: string }>;
}> = {
    terminal: { label: "终端", icon: TerminalSquare },
    artifacts: { label: "产物", icon: Box },
    todos: { label: "任务", icon: ListTodo },
    diff: { label: "Diff", icon: GitCompareArrows },
    runtime: { label: "运行", icon: Route },
};

function normalizedStatus(value: unknown) {
    return String(value || "").trim().toLowerCase();
}

function isActiveProcess(process: AdminProcessRef) {
    const status = normalizedStatus(process.status);
    return status !== "stopped" && status !== "terminated" && status !== "completed" && status !== "failed";
}

function collectArtifacts(messages: Message[]): WorkbenchArtifact[] {
    const byId = new Map<string, WorkbenchArtifact>();
    for (const message of messages) {
        for (const artifact of message.artifacts || []) {
            if (artifact?.id) {
                byId.set(artifact.id, { ...artifact, sourceMessageId: message.id });
            }
        }
        for (const node of message.nodes || []) {
            if (node.kind === "artifact" && node.artifact?.id) {
                byId.set(node.artifact.id, { ...node.artifact, sourceMessageId: message.id });
            }
        }
    }
    return Array.from(byId.values()).slice(-80).reverse();
}

function textOf(value: unknown) {
    return String(value || "").trim();
}

function getExecutionTitle(node: UiExecutionNode) {
    const data = node.data && typeof node.data === "object" ? node.data as Record<string, unknown> : {};
    return textOf(data.path)
        || textOf(data.file)
        || textOf(data.targetPath)
        || textOf(data.command)
        || textOf(node.label)
        || textOf(node.toolName)
        || "变更线索";
}

function collectDiffHints(messages: Message[]): DiffHint[] {
    const hints: DiffHint[] = [];
    const seen = new Set<string>();
    const push = (hint: DiffHint) => {
        if (seen.has(hint.id)) return;
        seen.add(hint.id);
        hints.push(hint);
    };

    for (const message of messages) {
        for (const node of message.nodes || []) {
            if (node.kind === "artifact") {
                const artifact = node.artifact;
                const cardType = inferArtifactCardType(artifact);
                if (cardType === "code" || cardType === "document") {
                    push({
                        id: `artifact:${artifact.id}`,
                        title: artifact.displayLabel || artifact.title || artifact.id,
                        subtitle: artifact.workspaceRelativePath || artifact.canonicalPath || artifact.displaySubtitle || "产物文件",
                        tone: "artifact",
                    });
                }
                continue;
            }
            if (node.kind !== "execution") continue;
            const toolName = textOf(node.toolName);
            if (!toolName) continue;
            if (/(write_native_file|apply_patch|patch|edit|share_workspace_file)/i.test(toolName)) {
                push({
                    id: `write:${node.id}`,
                    title: getExecutionTitle(node),
                    subtitle: toolName,
                    tone: "write",
                });
            } else if (/(run_system_command|execute_system_command|command_session_broker)/i.test(toolName)) {
                const title = getExecutionTitle(node);
                if (/(git diff|git status|npm run|pytest|pnpm|yarn|tsc|eslint)/i.test(title)) {
                    push({
                        id: `cmd:${node.id}`,
                        title,
                        subtitle: toolName,
                        tone: "command",
                    });
                }
            }
        }
    }
    return hints.slice(-40).reverse();
}

function normalizeTodos(items: TodoHudItem[]) {
    return items
        .map((item, index) => ({
            id: String(item.id || `todo-${index}`),
            text: String(item.content || item.text || "").trim(),
            status: normalizedStatus(item.status || "pending"),
        }))
        .filter((item) => item.text);
}

function WorkbenchRail({
    activeTab,
    open,
    counts,
    onSelect,
}: {
    activeTab: WorkbenchTab;
    open: boolean;
    counts: Record<WorkbenchTab, number>;
    onSelect: (tab: WorkbenchTab) => void;
}) {
    const tabs: WorkbenchTab[] = ["terminal", "artifacts", "todos", "diff", "runtime"];
    return (
        <div className="pointer-events-auto flex flex-col gap-1.5 rounded-2xl border border-border/70 bg-background/88 p-1.5 shadow-[0_18px_60px_rgba(15,23,42,0.14)] backdrop-blur-xl dark:border-white/10 dark:bg-stone-950/78">
            {tabs.map((tab) => {
                const Icon = tabMeta[tab].icon;
                const selected = open && activeTab === tab;
                const count = counts[tab] || 0;
                return (
                    <button
                        key={tab}
                        type="button"
                        aria-label={tabMeta[tab].label}
                        title={tabMeta[tab].label}
                        onClick={() => onSelect(tab)}
                        className={cn(
                            "relative flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground transition-all hover:bg-muted hover:text-foreground",
                            selected && "bg-primary text-primary-foreground shadow-sm hover:bg-primary hover:text-primary-foreground",
                        )}
                    >
                        <Icon className="h-4 w-4" />
                        {count > 0 ? (
                            <span className={cn(
                                "absolute -right-1 -top-1 min-w-[16px] rounded-full border border-background px-1 text-[9px] font-semibold leading-4",
                                selected ? "bg-foreground text-background" : "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900",
                            )}>
                                {Math.min(count, 99)}
                            </span>
                        ) : null}
                    </button>
                );
            })}
        </div>
    );
}

function PanelSection({
    title,
    icon: Icon,
    children,
    defaultOpen = true,
}: {
    title: string;
    icon: React.ElementType<{ className?: string }>;
    children: React.ReactNode;
    defaultOpen?: boolean;
}) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <section className="rounded-2xl border border-border/60 bg-background/74 shadow-sm backdrop-blur">
            <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
                onClick={() => setOpen((current) => !current)}
            >
                <Icon className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-semibold">{title}</span>
                <ChevronDown className={cn("ml-auto h-4 w-4 text-muted-foreground transition-transform", !open && "-rotate-90")} />
            </button>
            {open ? <div className="border-t border-border/50 p-3">{children}</div> : null}
        </section>
    );
}

function EmptyLine({ children }: { children: React.ReactNode }) {
    return (
        <div className="rounded-xl border border-dashed border-border/70 px-3 py-4 text-center text-xs leading-5 text-muted-foreground">
            {children}
        </div>
    );
}

export function WorkspaceWorkbenchPanel({
    open,
    activeTab,
    onOpenChange,
    onTabChange,
    workspacePath,
    messages,
    processes,
    todos,
    todoStale,
    runtimeModel,
    selectedRuntimeId,
    onSelectRuntime,
    onOpenRuntimeDetail,
}: WorkspaceWorkbenchPanelProps) {
    const setActiveArtifactId = useChatStore((state) => state.setActiveArtifactId);
    const artifacts = useMemo(() => collectArtifacts(messages), [messages]);
    const diffHints = useMemo(() => collectDiffHints(messages), [messages]);
    const normalizedTodos = useMemo(() => normalizeTodos(todos), [todos]);
    const activeProcesses = useMemo(() => processes.filter(isActiveProcess), [processes]);
    const counts = useMemo<Record<WorkbenchTab, number>>(() => ({
        terminal: activeProcesses.length,
        artifacts: artifacts.length,
        todos: normalizedTodos.length,
        diff: diffHints.length,
        runtime: runtimeModel.items.filter((item) => item.status !== "idle" || item.eventCount > 0).length,
    }), [activeProcesses.length, artifacts.length, diffHints.length, normalizedTodos.length, runtimeModel.items]);

    const selectTab = (tab: WorkbenchTab) => {
        onTabChange(tab);
        onOpenChange(true);
    };

    const activeMeta = tabMeta[activeTab];
    const ActiveIcon = activeMeta.icon;

    return (
        <div className="pointer-events-none fixed right-3 top-[5.75rem] z-[70] flex max-h-[calc(100vh-7rem)] items-start gap-2 lg:right-4">
            {open ? (
                <aside className="pointer-events-auto flex max-h-[calc(100vh-7rem)] w-[min(380px,calc(100vw-5.75rem))] flex-col overflow-hidden rounded-[24px] border border-border/70 bg-background/94 shadow-[0_24px_90px_rgba(15,23,42,0.18)] backdrop-blur-2xl dark:border-white/10 dark:bg-stone-950/92">
                    <header className="flex items-center gap-3 border-b border-border/60 px-4 py-3">
                        <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                            <ActiveIcon className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                            <div className="text-sm font-semibold">{activeMeta.label}</div>
                            <div className="truncate text-[11px] text-muted-foreground">
                                {workspacePath || "当前会话未绑定工作区"}
                            </div>
                        </div>
                        <button
                            type="button"
                            aria-label="关闭工作台"
                            title="关闭工作台"
                            onClick={() => onOpenChange(false)}
                            className="ml-auto flex h-8 w-8 items-center justify-center rounded-xl text-muted-foreground transition hover:bg-muted hover:text-foreground"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </header>

                    <div className="custom-scrollbar flex-1 space-y-3 overflow-y-auto p-3">
                        {activeTab === "terminal" ? (
                            <>
                                <PanelSection title="当前路径" icon={TerminalSquare}>
                                    <div className="rounded-xl bg-muted/55 px-3 py-2 font-mono text-xs leading-5 text-foreground/85">
                                        {workspacePath || "未绑定工作区"}
                                    </div>
                                </PanelSection>
                                <PanelSection title="会话进程" icon={TerminalSquare}>
                                    {processes.length > 0 ? (
                                        <div className="space-y-2">
                                            {processes.map((process) => (
                                                <InteractiveTerminalCard key={process.processId} process={process} compact />
                                            ))}
                                        </div>
                                    ) : (
                                        <EmptyLine>还没有由当前会话启动的终端进程。</EmptyLine>
                                    )}
                                </PanelSection>
                            </>
                        ) : null}

                        {activeTab === "artifacts" ? (
                            <PanelSection title="产物" icon={Box}>
                                {artifacts.length > 0 ? (
                                    <div className="space-y-2">
                                        {artifacts.map((artifact) => (
                                            <button
                                                key={artifact.id}
                                                type="button"
                                                onClick={() => setActiveArtifactId(artifact.id)}
                                                className="w-full rounded-xl border border-border/60 bg-background px-3 py-2 text-left transition hover:border-primary/35 hover:bg-primary/5"
                                            >
                                                <div className="flex items-center gap-2">
                                                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                                                    <span className="min-w-0 truncate text-sm font-medium">
                                                        {artifact.displayLabel || artifact.title || artifact.id}
                                                    </span>
                                                </div>
                                                <div className="mt-1 truncate text-[11px] text-muted-foreground">
                                                    {artifact.workspaceRelativePath || artifact.canonicalPath || artifact.displaySubtitle || artifact.kind || "产物"}
                                                </div>
                                            </button>
                                        ))}
                                    </div>
                                ) : (
                                    <EmptyLine>当前会话还没有产物。</EmptyLine>
                                )}
                            </PanelSection>
                        ) : null}

                        {activeTab === "todos" ? (
                            <PanelSection title={todoStale ? "任务进度 · 可能过期" : "任务进度"} icon={ListTodo}>
                                {normalizedTodos.length > 0 ? (
                                    <div className="space-y-2">
                                        {normalizedTodos.map((todo) => {
                                            const done = todo.status === "done" || todo.status === "skipped";
                                            return (
                                                <div key={todo.id} className="flex gap-2 rounded-xl border border-border/60 bg-background px-3 py-2">
                                                    <span className={cn("mt-1 h-2 w-2 rounded-full", done ? "bg-emerald-500" : todo.status === "in_progress" ? "bg-primary" : "bg-muted-foreground/35")} />
                                                    <div className="min-w-0 flex-1">
                                                        <div className={cn("text-sm leading-5", done && "text-muted-foreground line-through")}>{todo.text}</div>
                                                        <div className="mt-0.5 text-[11px] text-muted-foreground">{todo.status || "pending"}</div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : (
                                    <EmptyLine>当前没有 Supervisor 编排进度。</EmptyLine>
                                )}
                            </PanelSection>
                        ) : null}

                        {activeTab === "diff" ? (
                            <PanelSection title="Diff 线索" icon={GitCompareArrows}>
                                {diffHints.length > 0 ? (
                                    <div className="space-y-2">
                                        {diffHints.map((hint) => (
                                            <div key={hint.id} className="rounded-xl border border-border/60 bg-background px-3 py-2">
                                                <div className="flex items-center gap-2">
                                                    <Code2 className={cn("h-4 w-4 shrink-0", hint.tone === "write" ? "text-primary" : "text-muted-foreground")} />
                                                    <span className="min-w-0 truncate text-sm font-medium">{hint.title}</span>
                                                </div>
                                                <div className="mt-1 truncate text-[11px] text-muted-foreground">{hint.subtitle}</div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <EmptyLine>还没有可展示的文件变更线索。</EmptyLine>
                                )}
                            </PanelSection>
                        ) : null}

                        {activeTab === "runtime" ? (
                            <PanelSection title="Runtime 状态" icon={Route}>
                                {runtimeModel.items.length > 0 ? (
                                    <div className="space-y-2">
                                        {runtimeModel.items.map((item) => (
                                            <button
                                                key={item.id}
                                                type="button"
                                                onClick={() => onSelectRuntime(item.id)}
                                                className={cn(
                                                    "w-full rounded-xl border border-border/60 bg-background px-3 py-2 text-left transition hover:border-primary/35 hover:bg-primary/5",
                                                    selectedRuntimeId === item.id && "border-primary/35 bg-primary/5",
                                                )}
                                            >
                                                <div className="flex items-center gap-2">
                                                    <span className={cn(
                                                        "h-2 w-2 rounded-full",
                                                        item.status === "active" ? "bg-emerald-500" : item.status === "attention" ? "bg-rose-500" : item.status === "recent" ? "bg-amber-500" : "bg-muted-foreground/30",
                                                    )} />
                                                    <span className="min-w-0 truncate text-sm font-medium">{item.label}</span>
                                                    {item.eventCount > 0 ? <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{item.eventCount}</span> : null}
                                                </div>
                                                <div className="mt-1 truncate text-[11px] text-muted-foreground">
                                                    {item.lastActivity || item.stepTitle || item.description}
                                                    {item.lastTimestamp ? ` · ${formatRelativeRuntimeTime(item.lastTimestamp)}` : ""}
                                                </div>
                                            </button>
                                        ))}
                                        <button
                                            type="button"
                                            onClick={onOpenRuntimeDetail}
                                            className="mt-1 inline-flex h-9 w-full items-center justify-center gap-2 rounded-xl border border-border/70 bg-muted/40 text-xs font-medium text-foreground transition hover:bg-muted"
                                        >
                                            <Maximize2 className="h-3.5 w-3.5" />
                                            展开执行地图
                                        </button>
                                    </div>
                                ) : (
                                    <EmptyLine>当前没有 runtime 状态。</EmptyLine>
                                )}
                            </PanelSection>
                        ) : null}
                    </div>
                </aside>
            ) : null}

            <WorkbenchRail activeTab={activeTab} open={open} counts={counts} onSelect={selectTab} />
        </div>
    );
}
