"use client";

import { useMemo, useState } from "react";
import { Box, CircleDot, Code2, FileText, ListTodo, TerminalSquare } from "lucide-react";
import type { AdminProcessRef } from "@v8/session-realtime";

import { createArtifactDocument } from "@/lib/workbench";
import { resolveAndOpenWorkspaceFile } from "@/lib/workbench-actions";
import { useWorkbenchStore } from "@/store/workbench-store";
import type { RuntimeStageModel } from "@/lib/runtime-stage";
import type { Message, UiExecutionNode } from "@/store/chat-types";
import type { RuntimeArtifact } from "@/lib/artifacts";
import type { TodoHudItem } from "./TodosHUD";

type WorkbenchArtifact = RuntimeArtifact & { sourceMessageId?: string };
type FileHint = { id: string; path: string; tool: string };

interface WorkspaceWorkbenchPanelProps {
    messages: Message[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
}

function text(value: unknown) {
    return String(value || "").trim();
}

function activeProcess(process: AdminProcessRef) {
    return !["stopped", "terminated", "completed", "failed"].includes(text(process.status).toLowerCase());
}

function collectArtifacts(messages: Message[]) {
    const byId = new Map<string, WorkbenchArtifact>();
    for (const message of messages) {
        for (const artifact of message.artifacts || []) {
            if (artifact?.id) byId.set(artifact.id, { ...artifact, sourceMessageId: message.id });
        }
        for (const node of message.nodes || []) {
            if (node.kind === "artifact" && node.artifact?.id) byId.set(node.artifact.id, { ...node.artifact, sourceMessageId: message.id });
        }
    }
    return Array.from(byId.values()).slice(-30).reverse();
}

function executionPath(node: UiExecutionNode) {
    const data = node.data && typeof node.data === "object" ? node.data as Record<string, unknown> : {};
    return text(data.path || data.file || data.targetPath || data.target_path || data.workspacePath || data.workspace_path);
}

function collectFileHints(messages: Message[]) {
    const hints = new Map<string, FileHint>();
    for (const message of messages) {
        for (const node of message.nodes || []) {
            if (node.kind !== "execution") continue;
            const tool = text(node.toolName);
            if (!/(write_native_file|read_native_file|apply_patch|patch|edit|share_workspace_file)/i.test(tool)) continue;
            const path = executionPath(node);
            if (!path) continue;
            hints.set(path, { id: `${node.id}:${path}`, path, tool });
        }
    }
    return Array.from(hints.values()).slice(-24).reverse();
}

function Section({ title, icon: Icon, children }: { title: string; icon: typeof Box; children: React.ReactNode }) {
    return (
        <section className="border-b border-border/55 last:border-b-0">
            <div className="flex h-8 items-center gap-2 px-2 text-[11px] font-medium text-foreground/85">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{title}</span>
            </div>
            <div className="border-t border-border/35">{children}</div>
        </section>
    );
}

function EmptyRow({ children }: { children: React.ReactNode }) {
    return <div className="px-3 py-3 text-[11px] leading-5 text-muted-foreground">{children}</div>;
}

export function WorkspaceWorkbenchPanel({
    messages,
    processes,
    todos,
    todoStale,
    runtimeModel,
}: WorkspaceWorkbenchPanelProps) {
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const [fileError, setFileError] = useState("");
    const artifacts = useMemo(() => collectArtifacts(messages), [messages]);
    const fileHints = useMemo(() => collectFileHints(messages), [messages]);
    const activeProcesses = useMemo(() => processes.filter(activeProcess), [processes]);
    const visibleTodos = useMemo(() => todos.filter((item) => text(item.content || item.text)), [todos]);
    const currentRuntime = useMemo(
        () => runtimeModel.items.find((item) => item.status === "attention")
            || runtimeModel.items.find((item) => item.status === "active")
            || null,
        [runtimeModel.items],
    );
    const hasSecondaryContent = visibleTodos.length > 0 || artifacts.length > 0 || fileHints.length > 0 || activeProcesses.length > 0;

    return (
        <div className="h-full min-h-0 overflow-auto bg-background">
            <div className="mx-auto w-full max-w-[760px]">
            {currentRuntime ? (
                <div className="flex min-h-10 items-center gap-2 border-b border-border/55 px-3 py-2 text-[11px]">
                    <CircleDot className={`h-3 w-3 shrink-0 ${currentRuntime.status === "attention" ? "text-amber-500" : "text-emerald-500"}`} />
                    <span className="font-medium text-foreground">{currentRuntime.status === "attention" ? "等待你的确认" : "任务进行中"}</span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">{currentRuntime.lastActivity || currentRuntime.stepTitle || currentRuntime.shortLabel || currentRuntime.label}</span>
                </div>
            ) : null}

            {visibleTodos.length ? <Section title="任务" icon={ListTodo}>
                {visibleTodos.slice(0, 20).map((todo, index) => {
                    const status = text(todo.status || "pending").toLowerCase();
                    return (
                        <div key={text(todo.id) || index} className="flex min-h-8 items-start gap-2 border-b border-border/30 px-3 py-1.5 text-[11px] last:border-b-0">
                            <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${status === "done" || status === "skipped" ? "bg-emerald-500" : status === "in_progress" ? "bg-primary" : "bg-muted-foreground/35"}`} />
                            <span className={`min-w-0 flex-1 leading-4 ${status === "done" || status === "skipped" ? "text-muted-foreground line-through" : ""}`}>{text(todo.content || todo.text)}</span>
                        </div>
                    );
                })}
            </Section> : null}

            {artifacts.length ? <Section title="产物" icon={Box}>
                {artifacts.map((artifact) => (
                    <button
                        key={artifact.id}
                        type="button"
                        onClick={() => openDocument(createArtifactDocument(artifact), { activate: true, mode: "split" })}
                        className="flex min-h-9 w-full items-center gap-2 border-b border-border/30 px-3 py-1.5 text-left text-[11px] last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                    >
                        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate font-medium">{artifact.displayLabel || artifact.title || artifact.id}</span>
                        <span className="max-w-[42%] truncate text-[10px] text-muted-foreground">{artifact.workspaceRelativePath || artifact.kind}</span>
                    </button>
                ))}
            </Section> : null}

            {fileHints.length || fileError ? <Section title="文件变更" icon={Code2}>
                {fileError ? <div className="border-b border-destructive/25 bg-destructive/5 px-3 py-2 text-[10px] text-destructive">{fileError}</div> : null}
                {fileHints.map((hint) => (
                    <button
                        key={hint.id}
                        type="button"
                        onClick={() => {
                            setFileError("");
                            void resolveAndOpenWorkspaceFile(hint.path).catch((reason) => setFileError(reason instanceof Error ? reason.message : String(reason)));
                        }}
                        className="flex min-h-9 w-full items-center gap-2 border-b border-border/30 px-3 py-1.5 text-left text-[11px] last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                    >
                        <Code2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">{hint.path}</span>
                    </button>
                ))}
            </Section> : null}

            {activeProcesses.length ? <Section title="后台任务" icon={TerminalSquare}>
                {activeProcesses.map((process) => (
                    <div key={process.processId} className="flex min-h-9 items-center gap-2 border-b border-border/30 px-3 py-1.5 text-[11px] last:border-b-0">
                        <TerminalSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">{process.title || "后台任务"}</span>
                        <span className="text-[10px] text-muted-foreground">运行中</span>
                    </div>
                ))}
            </Section> : null}

            {!hasSecondaryContent && !currentRuntime ? <EmptyRow>{todoStale ? "任务信息正在更新。" : "当前没有需要关注的内容。"}</EmptyRow> : null}
            </div>
        </div>
    );
}
