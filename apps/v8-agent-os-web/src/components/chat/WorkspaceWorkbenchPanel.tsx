"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Box, ChevronDown, ChevronRight, CircleDot, FileText, ListTodo, TerminalSquare, Users } from "lucide-react";
import {
    buildSessionOutputProjection,
    buildSubagentReturnProjection,
    type AdminProcessRef,
    type SessionOutputProjection,
    type SubagentReturnProjection,
} from "@v8/session-realtime";

import { createArtifactDocument, createSubagentActivityDocument } from "@/lib/workbench";
import { resolveAndOpenWorkspaceFile } from "@/lib/workbench-actions";
import { useWorkbenchStore } from "@/store/workbench-store";
import type { RuntimeStageModel } from "@/lib/runtime-stage";
import type { Message } from "@/store/chat-types";
import { normalizeRuntimeArtifact } from "@/lib/artifacts";
import type { TodoHudItem } from "./TodosHUD";

interface WorkspaceWorkbenchPanelProps {
    sessionId: string;
    messages: Message[];
    outputEvidence?: unknown[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
}

function text(value: unknown) {
    return String(value || "").trim();
}

function normalizedPath(value: unknown) {
    return text(value).replace(/\\/g, "/");
}

function parentPathOf(path: string) {
    const parts = normalizedPath(path).split("/").filter(Boolean);
    return parts.slice(0, -1).join("/");
}

function activeProcess(process: AdminProcessRef) {
    return !["stopped", "terminated", "completed", "failed"].includes(text(process.status).toLowerCase());
}

function Section({ title, icon: Icon, count, children, defaultOpen = true }: { title: string; icon: typeof Box; count?: number; children: React.ReactNode; defaultOpen?: boolean }) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <section className="border-b border-border/55 last:border-b-0">
            <button type="button" onClick={() => setOpen((value) => !value)} className="flex h-9 w-full items-center gap-2 px-2 text-left text-[11px] font-medium text-foreground/85 hover:bg-muted/25 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="flex-1">{title}</span>
                {typeof count === "number" ? <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{count}</span> : null}
                {open ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
            </button>
            {open ? <div className="border-t border-border/35">{children}</div> : null}
        </section>
    );
}

function EmptyRow({ children }: { children: React.ReactNode }) {
    return <div className="px-3 py-3 text-[11px] leading-5 text-muted-foreground">{children}</div>;
}

function subagentColorSeed(value: string) {
    let hash = 0;
    for (const char of value || "subagent") hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return Math.abs(hash) % 360;
}

function SubagentAvatar({ item }: { item: SubagentReturnProjection }) {
    if (item.avatar) {
        return <img src={item.avatar} alt="" className="h-7 w-7 shrink-0 rounded-lg object-cover" />;
    }
    const hue = subagentColorSeed(item.family || item.name);
    return (
        <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border text-[11px] font-semibold"
            style={{ backgroundColor: `hsl(${hue} 74% 92%)`, borderColor: `hsl(${hue} 58% 70%)`, color: `hsl(${hue} 62% 28%)` }}
            aria-hidden="true"
        >
            {Array.from(item.name.trim())[0]?.toUpperCase() || "A"}
        </span>
    );
}

function subagentStatusLabel(status: string) {
    if (["ok", "completed", "success", "terminated"].includes(status)) return "已回流";
    if (["queued", "running", "starting", "streaming", "updated"].includes(status)) return "进行中";
    return "需要处理";
}

function SubagentReturnRow({ item, onOpen, nested = false }: { item: SubagentReturnProjection; onOpen: (item: SubagentReturnProjection) => void; nested?: boolean }) {
    return (
        <div className={`${nested ? "ml-5 border-l border-border/45" : ""} border-b border-border/30 last:border-b-0`}>
            <button
                type="button"
                onClick={() => onOpen(item)}
                className="group flex min-h-11 w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] hover:bg-muted/35 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
            >
                <SubagentAvatar item={item} />
                <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-foreground">{item.name}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">{item.taskGoal || item.summary || "已返回协作结果"}</span>
                </span>
                <span className="shrink-0 text-[10px] text-muted-foreground">{subagentStatusLabel(item.status)}</span>
                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </button>
        </div>
    );
}

function outputSubtitle(output: SessionOutputProjection): string {
    if (output.path) return parentPathOf(output.path) || output.source;
    return output.kind || output.mimeType || output.source;
}

export function WorkspaceWorkbenchPanel({
    sessionId,
    messages,
    outputEvidence = [],
    processes,
    todos,
    todoStale,
    runtimeModel,
}: WorkspaceWorkbenchPanelProps) {
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const [fileError, setFileError] = useState("");
    const [runtimeArtifacts, setRuntimeArtifacts] = useState<Array<Record<string, unknown>>>([]);
    const subagentReturns = useMemo(
        () => buildSubagentReturnProjection(messages, runtimeModel.messageActivities.map((activity) => activity.node)),
        [messages, runtimeModel.messageActivities],
    );
    const outputs = useMemo(
        () => buildSessionOutputProjection(messages, runtimeArtifacts, { sessionId, evidence: outputEvidence }),
        [messages, outputEvidence, runtimeArtifacts, sessionId],
    );
    const activeProcesses = useMemo(() => processes.filter(activeProcess), [processes]);
    const visibleTodos = useMemo(() => todos.filter((item) => text(item.content || item.text)), [todos]);
    const currentRuntime = useMemo(
        () => runtimeModel.items.find((item) => item.status === "attention")
            || runtimeModel.items.find((item) => item.status === "active")
            || null,
        [runtimeModel.items],
    );
    const hasSecondaryContent = visibleTodos.length > 0 || subagentReturns.length > 0 || outputs.length > 0 || activeProcesses.length > 0;

    const openSubagentReturn = useCallback((item: SubagentReturnProjection) => {
        openDocument(createSubagentActivityDocument({
            sessionId,
            delegationId: item.delegationId || item.id,
            title: item.name,
        }), { activate: true, mode: "split" });
    }, [openDocument, sessionId]);

    const openOutput = useCallback((output: SessionOutputProjection) => {
        setFileError("");
        if (output.path) {
            void resolveAndOpenWorkspaceFile(output.path, { sessionId }).catch((reason) => {
                setFileError(reason instanceof Error ? reason.message : String(reason));
            });
            return;
        }
        const artifact = output.rawArtifact ? normalizeRuntimeArtifact(output.rawArtifact) : null;
        if (artifact) openDocument(createArtifactDocument(artifact), { activate: true, mode: "split" });
    }, [openDocument, sessionId]);

    useEffect(() => {
        if (!sessionId) {
            setRuntimeArtifacts([]);
            return;
        }
        const controller = new AbortController();
        const query = new URLSearchParams({ sessionId, limit: "100" });
        void fetch(`/api/artifacts?${query.toString()}`, { cache: "no-store", signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then((payload) => {
                if (!controller.signal.aborted) {
                    setRuntimeArtifacts(Array.isArray(payload?.artifacts) ? payload.artifacts : []);
                }
            })
            .catch(() => {
                if (!controller.signal.aborted) setRuntimeArtifacts([]);
            });
        return () => controller.abort();
    }, [sessionId]);

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

            {visibleTodos.length ? <Section title="任务" icon={ListTodo} count={visibleTodos.length}>
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

            {subagentReturns.length ? <Section title="子 Agent" icon={Users} count={subagentReturns.length}>
                {subagentReturns.map((item) => <SubagentReturnRow key={item.id} item={item} onOpen={openSubagentReturn} />)}
            </Section> : null}

            {outputs.length || fileError ? <Section title="产物" icon={Box} count={outputs.length}>
                {fileError ? <div className="border-b border-destructive/25 bg-destructive/5 px-3 py-2 text-[10px] text-destructive">{fileError}</div> : null}
                {outputs.map((output) => (
                    <button
                        key={output.id}
                        type="button"
                        onClick={() => openOutput(output)}
                        className="group flex min-h-10 w-full items-center gap-2 border-b border-border/30 px-3 py-1.5 text-left text-[11px] last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                    >
                        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium">{output.name}</span>
                            <span className="block truncate text-[10px] text-muted-foreground">{outputSubtitle(output)}</span>
                        </span>
                        {output.statusLabel ? <span className="shrink-0 text-[10px] text-muted-foreground">{output.statusLabel}</span> : null}
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                    </button>
                ))}
            </Section> : null}

            {activeProcesses.length ? <Section title="后台任务" icon={TerminalSquare} count={activeProcesses.length} defaultOpen={false}>
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
