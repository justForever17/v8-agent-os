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
    messages: Message[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
    selectedRuntimeId: RuntimeId | null;
    onSelectRuntime: (runtimeId: RuntimeId) => void;
    onOpenRuntimeDetail: () => void;
}

function normalizedStatus(value: unknown) {
    return String(value || "").trim().toLowerCase();
}

function isActiveProcess(process: AdminProcessRef) {
    const status = normalizedStatus(process.status);
    return status !== "stopped" && status !== "terminated" && status !== "completed" && status !== "failed";
}

function buildRuntimeWorkbenchSummary(runtimeModel: RuntimeStageModel) {
    if (runtimeModel.items.length === 0) {
        return null;
    }
    const focusItem = runtimeModel.items.find((item) => item.status === "attention")
        || runtimeModel.items.find((item) => item.status === "active")
        || [...runtimeModel.items].sort((left, right) => (right.lastTimestamp || 0) - (left.lastTimestamp || 0))[0]
        || runtimeModel.items[0];
    const totalEvents = runtimeModel.items.reduce((total, item) => total + item.eventCount, 0);
    return {
        focusItem,
        totalEvents,
        runtimeCount: runtimeModel.items.length,
        summary: focusItem.lastActivity || focusItem.stepTitle || focusItem.description,
    };
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

    const runtimeSummary = useMemo(
        () => buildRuntimeWorkbenchSummary(runtimeModel),
        [runtimeModel],
    );
    const hasRuntimeActivity = Boolean(runtimeSummary);

    const hasAnyContent = activeProcesses.length > 0 || artifacts.length > 0 || normalizedTodos.length > 0 || diffHints.length > 0 || hasRuntimeActivity;

    return (
        <div className="w-full flex flex-col gap-3">
            {!hasAnyContent ? (
                <EmptyLine>
                    <div className="py-4 space-y-1">
                        <div className="font-semibold text-foreground/85">工作台无活跃内容</div>
                        <div className="text-[10px] text-muted-foreground/75 leading-relaxed">
                            当 Agent 在对话中生成了新文件（产物）、编排了任务、产生了代码变更线索或触发了后台长任务时，对应模块会自动在此浮现。
                        </div>
                    </div>
                </EmptyLine>
            ) : (
                <>
                    {/* 活跃进程 (Active Processes) -> InteractiveTerminalCard */}
                    {activeProcesses.length > 0 && (
                        <PanelSection title="活跃进程" icon={TerminalSquare}>
                            <div className="space-y-2">
                                {activeProcesses.map((process) => (
                                    <InteractiveTerminalCard key={process.processId} process={process} compact />
                                ))}
                            </div>
                        </PanelSection>
                    )}

                    {/* 产物 (Artifacts) */}
                    {artifacts.length > 0 && (
                        <PanelSection title="生成产物" icon={Box}>
                            <div className="space-y-1.5 max-h-56 overflow-y-auto pr-0.5 custom-scrollbar">
                                {artifacts.map((artifact) => (
                                    <button
                                        key={artifact.id}
                                        type="button"
                                        onClick={() => setActiveArtifactId(artifact.id)}
                                        className="w-full rounded-xl border border-border/50 bg-background/60 px-3 py-2 text-left transition hover:border-primary/35 hover:bg-primary/5"
                                    >
                                        <div className="flex items-center gap-2">
                                            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                                            <span className="min-w-0 truncate text-xs font-medium">
                                                {artifact.displayLabel || artifact.title || artifact.id}
                                            </span>
                                        </div>
                                        <div className="mt-0.5 truncate text-[10px] text-muted-foreground/85">
                                            {artifact.workspaceRelativePath || artifact.canonicalPath || artifact.displaySubtitle || artifact.kind || "产物"}
                                        </div>
                                    </button>
                                ))}
                            </div>
                        </PanelSection>
                    )}

                    {/* 任务进度 (Todos) */}
                    {normalizedTodos.length > 0 && (
                        <PanelSection title={todoStale ? "任务进度 · 可能过期" : "任务进度"} icon={ListTodo}>
                            <div className="space-y-2 max-h-56 overflow-y-auto pr-0.5 custom-scrollbar">
                                {normalizedTodos.map((todo) => {
                                    const done = todo.status === "done" || todo.status === "skipped";
                                    return (
                                        <div key={todo.id} className="flex gap-2 rounded-xl border border-border/50 bg-background/60 px-3 py-2 text-xs">
                                            <span className={cn("mt-1.5 h-1.5 w-1.5 rounded-full shrink-0", done ? "bg-emerald-500" : todo.status === "in_progress" ? "bg-primary" : "bg-muted-foreground/35")} />
                                            <div className="min-w-0 flex-1">
                                                <div className={cn("leading-relaxed", done && "text-muted-foreground line-through")}>{todo.text}</div>
                                                <div className="mt-0.5 text-[10px] text-muted-foreground/75">{todo.status || "pending"}</div>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </PanelSection>
                    )}

                    {/* Diff 线索 (Diff Hints) */}
                    {diffHints.length > 0 && (
                        <PanelSection title="Diff 线索" icon={GitCompareArrows}>
                            <div className="space-y-2 max-h-56 overflow-y-auto pr-0.5 custom-scrollbar">
                                {diffHints.map((hint) => (
                                    <div key={hint.id} className="rounded-xl border border-border/50 bg-background/60 px-3 py-2 text-xs">
                                        <div className="flex items-center gap-2">
                                            <Code2 className={cn("h-4 w-4 shrink-0", hint.tone === "write" ? "text-primary" : "text-muted-foreground")} />
                                            <span className="min-w-0 truncate font-medium">{hint.title}</span>
                                        </div>
                                        <div className="mt-0.5 truncate text-[10px] text-muted-foreground/80">{hint.subtitle}</div>
                                    </div>
                                ))}
                            </div>
                        </PanelSection>
                    )}

                    {/* Runtime 状态 (Runtime) */}
                    {runtimeSummary && (
                        <PanelSection title="Runtime 状态" icon={Route}>
                            <div className="space-y-1.5">
                                <button
                                    type="button"
                                    onClick={() => {
                                        onSelectRuntime(runtimeSummary.focusItem.id);
                                        onOpenRuntimeDetail();
                                    }}
                                    className={cn(
                                        "w-full rounded-xl border border-border/50 bg-background/60 px-3 py-2 text-left transition hover:border-primary/35 hover:bg-primary/5",
                                        selectedRuntimeId === runtimeSummary.focusItem.id && "border-primary/35 bg-primary/5",
                                    )}
                                >
                                    <div className="flex items-center gap-2">
                                        <span className={cn(
                                            "h-1.5 w-1.5 rounded-full shrink-0",
                                            runtimeSummary.focusItem.status === "active" ? "bg-emerald-500" : runtimeSummary.focusItem.status === "attention" ? "bg-rose-500" : "bg-amber-500",
                                        )} />
                                        <span className="min-w-0 truncate text-xs font-medium">{runtimeSummary.focusItem.label}</span>
                                        <span className="ml-auto rounded-full bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">
                                            {runtimeSummary.runtimeCount} 面 · {runtimeSummary.totalEvents} 条
                                        </span>
                                    </div>
                                    <div className="mt-0.5 truncate text-[10px] text-muted-foreground/80">
                                        {runtimeSummary.summary}
                                        {runtimeSummary.focusItem.lastTimestamp ? ` · ${formatRelativeRuntimeTime(runtimeSummary.focusItem.lastTimestamp)}` : ""}
                                    </div>
                                    <div className="mt-2 inline-flex items-center gap-1.5 text-[10px] font-medium text-foreground/75">
                                        <Maximize2 className="h-3.5 w-3.5" />
                                        查看执行地图
                                    </div>
                                </button>
                            </div>
                        </PanelSection>
                    )}
                </>
            )}
        </div>
    );
}
