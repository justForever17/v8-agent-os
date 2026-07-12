"use client";

import { useEffect, useMemo, useState } from "react";
import { Box, ChevronDown, ChevronRight, CircleDot, Code2, FileText, FolderOpen, ListTodo, TerminalSquare } from "lucide-react";
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
type SpecDocumentSummary = {
    id: string;
    path: string;
    fileName: string;
    groupKey: string;
    groupLabel: string;
    statusLabel: string;
};

interface WorkspaceWorkbenchPanelProps {
    messages: Message[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
    workspacePath?: string;
}

function text(value: unknown) {
    return String(value || "").trim();
}

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function normalizedPath(value: unknown) {
    return text(value).replace(/\\/g, "/");
}

function fileNameOf(path: string) {
    return normalizedPath(path).split("/").filter(Boolean).at(-1) || path;
}

function parentPathOf(path: string) {
    const parts = normalizedPath(path).split("/").filter(Boolean);
    return parts.slice(0, -1).join("/");
}

function collectSpecDocuments(value: unknown): SpecDocumentSummary[] {
    const root = recordOf(value);
    const specs = Array.isArray(root.specs) ? root.specs : [];
    const documents: SpecDocumentSummary[] = [];
    for (const rawSpec of specs) {
        const spec = recordOf(rawSpec);
        const specId = text(spec.specId || spec.spec_id);
        const featureName = text(spec.featureName || spec.feature_name);
        const stageDocuments = recordOf(spec.documents);
        const pipeline = recordOf(spec.pipelineControl || spec.pipeline_control);
        const blockedStage = text(pipeline.blockedByApproval || pipeline.blocked_by_approval).toLowerCase();
        const staleStages = recordOf(spec.staleStages || spec.stale_stages);
        const appendDocument = (documentKey: string, rawDocument: unknown, fallbackStatus = "") => {
            const document = recordOf(rawDocument);
            const path = normalizedPath(document.relativePath || document.relative_path);
            if (!path) return;
            const status = text(document.status).toLowerCase();
            const statusLabel = recordOf(staleStages[documentKey]).stale
                ? "需更新"
                : status === "approved"
                    ? "已同意"
                    : blockedStage === documentKey
                        ? "待确认"
                        : fallbackStatus || "可查看";
            const groupPath = parentPathOf(path);
            documents.push({
                id: `spec-document:${specId || groupPath}:${documentKey}:${path}`,
                path,
                fileName: fileNameOf(path),
                groupKey: specId || groupPath,
                groupLabel: featureName || fileNameOf(groupPath) || "规格文档",
                statusLabel,
            });
        };
        for (const [stage, document] of Object.entries(stageDocuments)) {
            appendDocument(stage, document);
        }
        const annexDocuments = recordOf(spec.annexDocuments || spec.annex_documents);
        for (const [name, document] of Object.entries(annexDocuments)) {
            appendDocument(`annex:${name}`, document, "附录");
        }
        const quality = recordOf(spec.qualityEvidence || spec.quality_evidence);
        const checklists = recordOf(quality.checklists);
        for (const [name, document] of Object.entries(checklists)) {
            appendDocument(`checklist:${name}`, document, "检查单");
        }
        const rawTargetDirectories = spec.targetOutputDirectories || spec.target_output_directories;
        const rawDeliverableFiles = spec.explicitDeliverableFiles || spec.explicit_deliverable_files;
        const targetDirectories = Array.isArray(rawTargetDirectories) ? rawTargetDirectories : [];
        const deliverableFiles = Array.isArray(rawDeliverableFiles) ? rawDeliverableFiles : [];
        for (const rawDirectory of targetDirectories) {
            const directory = normalizedPath(rawDirectory).replace(/\/$/, "");
            if (!directory) continue;
            for (const rawFile of deliverableFiles) {
                const fileName = fileNameOf(text(rawFile));
                if (!fileName) continue;
                const path = normalizedPath(`${directory}/${fileName}`);
                documents.push({
                    id: `spec-deliverable:${specId || directory}:${path}`,
                    path,
                    fileName,
                    groupKey: `${specId || directory}:deliverables:${directory}`,
                    groupLabel: fileNameOf(directory) || "交付文件",
                    statusLabel: pipeline.runtimeExecutionAllowed ? "交付文件" : "待生成",
                });
            }
        }
    }
    return documents.slice(0, 48);
}

function collectMessageSpecDocuments(messages: Message[]) {
    const specs = messages
        .map((message) => recordOf(message.metadata).specBrief || recordOf(message.metadata).spec_brief)
        .filter((value) => Object.keys(recordOf(value)).length > 0);
    return collectSpecDocuments({ specs });
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

export function WorkspaceWorkbenchPanel({
    messages,
    processes,
    todos,
    todoStale,
    runtimeModel,
    workspacePath,
}: WorkspaceWorkbenchPanelProps) {
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const workbenchTabs = useWorkbenchStore((state) => state.tabs);
    const [fileError, setFileError] = useState("");
    const [listedSpecDocuments, setListedSpecDocuments] = useState<SpecDocumentSummary[]>([]);
    const artifacts = useMemo(() => collectArtifacts(messages), [messages]);
    const fileHints = useMemo(() => collectFileHints(messages), [messages]);
    const liveSpecDocuments = useMemo(() => workbenchTabs.flatMap((tab) => {
        if (tab.document.kind !== "workspace_file") return [];
        const path = text(tab.document.subjectRef.workspacePath).replace(/\\/g, "/");
        if (!/(?:^|\/)\.v8\/specs\//i.test(path)) return [];
        return [{
            id: tab.document.documentId,
            path,
            fileName: fileNameOf(path),
            groupKey: parentPathOf(path),
            groupLabel: fileNameOf(parentPathOf(path)) || "规格文档",
            statusLabel: "可查看",
        } satisfies SpecDocumentSummary];
    }), [workbenchTabs]);
    const messageSpecDocuments = useMemo(() => collectMessageSpecDocuments(messages), [messages]);
    const specDocuments = useMemo(() => {
        const byPath = new Map<string, SpecDocumentSummary>();
        for (const document of liveSpecDocuments) byPath.set(document.path, document);
        for (const document of messageSpecDocuments) byPath.set(document.path, document);
        for (const document of listedSpecDocuments) byPath.set(document.path, document);
        return Array.from(byPath.values());
    }, [listedSpecDocuments, liveSpecDocuments, messageSpecDocuments]);
    const specDocumentGroups = useMemo(() => {
        const grouped = new Map<string, { key: string; label: string; documents: SpecDocumentSummary[] }>();
        for (const document of specDocuments) {
            const existing = grouped.get(document.groupKey);
            if (existing) existing.documents.push(document);
            else grouped.set(document.groupKey, { key: document.groupKey, label: document.groupLabel, documents: [document] });
        }
        return Array.from(grouped.values());
    }, [specDocuments]);
    const activeProcesses = useMemo(() => processes.filter(activeProcess), [processes]);
    const visibleTodos = useMemo(() => todos.filter((item) => text(item.content || item.text)), [todos]);
    const currentRuntime = useMemo(
        () => runtimeModel.items.find((item) => item.status === "attention")
            || runtimeModel.items.find((item) => item.status === "active")
            || null,
        [runtimeModel.items],
    );
    const hasSecondaryContent = visibleTodos.length > 0 || specDocuments.length > 0 || artifacts.length > 0 || fileHints.length > 0 || activeProcesses.length > 0;

    useEffect(() => {
        const normalizedWorkspacePath = text(workspacePath);
        if (!normalizedWorkspacePath) {
            setListedSpecDocuments([]);
            return;
        }
        const controller = new AbortController();
        const query = new URLSearchParams({
            workspace_path: normalizedWorkspacePath,
            include_archived: "true",
            limit: "10",
        });
        void fetch(`/api/specs?${query.toString()}`, { cache: "no-store", signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then((payload) => {
                if (!controller.signal.aborted) setListedSpecDocuments(collectSpecDocuments(payload));
            })
            .catch(() => {
                if (!controller.signal.aborted) setListedSpecDocuments([]);
            });
        return () => controller.abort();
    }, [workspacePath]);

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

            {specDocuments.length || artifacts.length ? <Section title="产物" icon={Box} count={specDocuments.length + artifacts.length}>
                {specDocumentGroups.map((group) => (
                    <details key={group.key} open className="group/spec border-b border-border/30 last:border-b-0">
                        <summary className="flex min-h-8 cursor-pointer list-none items-center gap-2 px-3 py-1.5 text-[11px] text-muted-foreground hover:bg-muted/25 [&::-webkit-details-marker]:hidden">
                            <FolderOpen className="h-3.5 w-3.5 shrink-0" />
                            <span className="min-w-0 flex-1 truncate">{group.label}</span>
                            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px]">{group.documents.length}</span>
                            <ChevronRight className="h-3.5 w-3.5 transition-transform group-open/spec:rotate-90" />
                        </summary>
                        <div className="border-t border-border/25 bg-muted/[0.08]">
                            {group.documents.map((document) => (
                                <button
                                    key={document.id}
                                    type="button"
                                    onClick={() => {
                                        setFileError("");
                                        void resolveAndOpenWorkspaceFile(document.path).catch((reason) => setFileError(reason instanceof Error ? reason.message : String(reason)));
                                    }}
                                    className="flex min-h-9 w-full items-center gap-2 border-b border-border/25 py-1.5 pl-7 pr-3 text-left text-[11px] last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                                >
                                    <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                    <span className="min-w-0 flex-1 truncate font-medium">{document.fileName}</span>
                                    <span className="shrink-0 text-[10px] text-muted-foreground">{document.statusLabel}</span>
                                </button>
                            ))}
                        </div>
                    </details>
                ))}
                {artifacts.map((artifact) => (
                    <button
                        key={artifact.id}
                        type="button"
                        onClick={() => openDocument(createArtifactDocument(artifact), { activate: true, mode: "split" })}
                        className="flex min-h-9 w-full items-center gap-2 border-b border-border/30 px-3 py-1.5 text-left text-[11px] last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                    >
                        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate font-medium">{artifact.displayLabel || artifact.title || fileNameOf(artifact.workspaceRelativePath || "") || "未命名产物"}</span>
                        <span className="max-w-[42%] truncate text-[10px] text-muted-foreground">{artifact.workspaceRelativePath || artifact.kind}</span>
                    </button>
                ))}
            </Section> : null}

            {fileHints.length || fileError ? <Section title="文件变更" icon={Code2} count={fileHints.length}>
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
                        <span className="min-w-0 flex-1 truncate font-medium">{fileNameOf(hint.path)}</span>
                        <span className="max-w-[45%] truncate text-[10px] text-muted-foreground">{parentPathOf(hint.path)}</span>
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
