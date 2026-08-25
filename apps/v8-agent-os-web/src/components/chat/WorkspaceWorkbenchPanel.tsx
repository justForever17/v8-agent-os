"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Blocks, Box, ChevronDown, ChevronRight, ChevronUp, CircleDot, Code2, Database, FileText, ListTodo, MousePointerClick, Paperclip, Search, Sparkles, TerminalSquare, Users, Workflow } from "lucide-react";
import {
    buildSessionOutputProjection,
    buildSessionSourceProjection,
    buildSubagentReturnProjection,
    isActiveCommandSessionStatus,
    type AdminProcessRef,
    type SessionOutputProjection,
    type SessionRuntimeId,
    type SessionSourceProjection,
    type SessionSourceRef,
    type SubagentReturnProjection,
} from "@v8/session-realtime";

import { createArtifactDocument, createExternalArtifactDocument, createRuntimeActivityDocument, createSubagentActivityDocument } from "@/lib/workbench";
import { resolveAndOpenWorkspaceFile } from "@/lib/workbench-actions";
import { useWorkbenchStore } from "@/store/workbench-store";
import { useT } from "@/components/providers/LocaleProvider";
import type { RuntimeStageCard, RuntimeStageModel } from "@/lib/runtime-stage";
import type { Message } from "@/store/chat-types";
import { normalizeRuntimeArtifact, prioritizeArtifactItems } from "@/lib/artifacts";
import type { TodoHudItem } from "./TodosHUD";

interface WorkspaceWorkbenchPanelProps {
    sessionId: string;
    messages: Message[];
    outputEvidence?: unknown[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
    pendingConfirmation?: boolean;
    onOpenPendingConfirmation?: () => void;
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

function humanSafeOutputPath(path: string) {
    const normalized = normalizedPath(path);
    if (
        !normalized
        || normalized.startsWith("/")
        || /^[A-Za-z]:\//.test(normalized)
        || /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(normalized)
    ) {
        return "";
    }
    return normalized;
}

function activeProcess(process: AdminProcessRef) {
    return isActiveCommandSessionStatus(process.status);
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

function subagentStatusLabel(status: string, t: ReturnType<typeof useT>) {
    if (["ok", "completed", "success", "terminated"].includes(status)) return t("web.workbench.subagent.returned");
    if (["queued", "running", "starting", "streaming", "updated"].includes(status)) return t("web.workbench.subagent.running");
    return t("web.workbench.subagent.failed");
}

function isFailedStatus(status: string) {
    return ["failed", "error", "cancelled", "degraded", "blocked"].includes(String(status || "").toLowerCase());
}

function SubagentReturnRow({ item, onOpen, nested = false }: { item: SubagentReturnProjection; onOpen: (item: SubagentReturnProjection) => void; nested?: boolean }) {
    const t = useT();
    return (
        <div className={`${nested ? "ml-5 border-l border-border/45" : ""} border-b border-border/30 last:border-b-0`}>
            <button
                data-v8-context-open-workbench
                type="button"
                onClick={() => onOpen(item)}
                className="group flex min-h-11 w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] hover:bg-muted/35 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
            >
                <SubagentAvatar item={item} />
                <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-1.5"><span className="truncate font-medium text-foreground">{item.name}</span>{item.roleLabel ? <span className="shrink-0 rounded-full border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[8px] text-muted-foreground">{item.roleLabel}</span> : null}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">{item.taskGoal || (isFailedStatus(item.status) ? item.summary : null) || t("web.workbench.subagent.defaultSummary")}</span>
                </span>
                <span className="shrink-0 text-[10px] text-muted-foreground">{subagentStatusLabel(item.status, t)}</span>
                <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </button>
            {item.children.map((child) => (
                <SubagentReturnRow key={child.id} item={child} onOpen={onOpen} nested />
            ))}
        </div>
    );
}

function isRuntimeActivityCard(item: RuntimeStageCard): item is RuntimeStageCard & { id: SessionRuntimeId } {
    return item.eventCount > 0 && !["chat", "subagent_swarm", "context_governance", "desktop_live"].includes(item.id);
}

const RUNTIME_ACTIVITY_ICONS: Partial<Record<SessionRuntimeId, typeof Activity>> = {
    research: Search,
    network_supervisor: Search,
    computer_use: MousePointerClick,
    engineering: Code2,
    engineering_lane: Code2,
    creative_media: Sparkles,
    rpa: Workflow,
    automation: Workflow,
    memory: Database,
    extensions: Blocks,
};

function RuntimeActivityRow({ item, onOpen }: { item: RuntimeStageCard & { id: SessionRuntimeId }; onOpen: (item: RuntimeStageCard & { id: SessionRuntimeId }) => void }) {
    const t = useT();
    const Icon = RUNTIME_ACTIVITY_ICONS[item.id] || Activity;
    const active = item.status === "active";
    const attention = item.status === "attention";
    return (
        <button
            data-v8-context-open-workbench
            data-runtime-activity-runtime={item.id}
            type="button"
            onClick={() => onOpen(item)}
            className="group flex min-h-11 w-full items-center gap-2 border-b border-border/30 px-3 py-1.5 text-left text-[11px] last:border-b-0 hover:bg-muted/35 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
        >
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border/65 bg-muted/25 text-muted-foreground">
                <Icon className={`h-3.5 w-3.5 ${active ? "animate-pulse text-primary" : attention ? "text-amber-500" : ""}`} />
            </span>
            <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-foreground">{item.label}</span>
                <span className="block truncate text-[10px] text-muted-foreground">{item.lastActivity || item.description}</span>
            </span>
            <span className="shrink-0 text-[9px] tabular-nums text-muted-foreground">{active ? t("web.workbench.runtimeActivity.running") : t("web.workbench.runtimeActivity.eventCount", { count: item.eventCount })}</span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </button>
    );
}

function outputSubtitle(output: SessionOutputProjection): string {
    const safePath = humanSafeOutputPath(output.path || "");
    if (safePath) return parentPathOf(safePath) || output.source;
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
    pendingConfirmation = false,
    onOpenPendingConfirmation,
}: WorkspaceWorkbenchPanelProps) {
    const t = useT();
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const [fileError, setFileError] = useState("");
    const [runtimeArtifacts, setRuntimeArtifacts] = useState<Array<Record<string, unknown>>>([]);
    const [sessionSources, setSessionSources] = useState<SessionSourceRef[]>([]);
    const [outputsExpanded, setOutputsExpanded] = useState(false);
    const resourceRevision = useMemo(
        () => runtimeModel.messageActivities
            .filter((activity) => {
                const topic = text(activity.topic).toLowerCase();
                return topic === "artifact.recorded"
                    || topic.startsWith("handoff.ref.")
                    || topic === "runtime.episode.handoff_ready";
            })
            .slice(-16)
            .map((activity) => `${activity.id}:${activity.timestamp || ""}`)
            .join("|"),
        [runtimeModel.messageActivities],
    );
    const subagentReturns = useMemo(
        () => buildSubagentReturnProjection(messages, runtimeModel.messageActivities.map((activity) => activity.node)),
        [messages, runtimeModel.messageActivities],
    );
    const outputs = useMemo(
        () => buildSessionOutputProjection(messages, runtimeArtifacts, { sessionId, evidence: outputEvidence }),
        [messages, outputEvidence, runtimeArtifacts, sessionId],
    );
    const prioritizedOutputs = useMemo(
        () => prioritizeArtifactItems(outputs, (output) => output.rawArtifact || output),
        [outputs],
    );
    const visibleOutputs = outputsExpanded ? prioritizedOutputs : prioritizedOutputs.slice(0, 5);
    const hiddenOutputCount = Math.max(0, prioritizedOutputs.length - 5);
    const sources = useMemo(
        () => buildSessionSourceProjection(messages, sessionSources),
        [messages, sessionSources],
    );
    const activeProcesses = useMemo(() => processes.filter(activeProcess), [processes]);
    const workbenchIsLive = useMemo(
        () => runtimeModel.items.some((item) => item.status === "active" || item.status === "attention"),
        [runtimeModel.items],
    );
    const visibleTodos = useMemo(() => todos.filter((item) => text(item.content || item.text)), [todos]);
    const currentRuntime = useMemo(
        () => runtimeModel.items.find((item) => item.status === "attention")
            || runtimeModel.items.find((item) => item.status === "active")
            || null,
        [runtimeModel.items],
    );
    const runtimeActivityCards = useMemo(
        () => runtimeModel.items.filter(isRuntimeActivityCard),
        [runtimeModel.items],
    );
    const hasSecondaryContent = visibleTodos.length > 0 || runtimeActivityCards.length > 0 || subagentReturns.length > 0 || outputs.length > 0 || sources.length > 0 || activeProcesses.length > 0;

    const openRuntimeActivity = useCallback((item: RuntimeStageCard & { id: SessionRuntimeId }) => {
        openDocument(createRuntimeActivityDocument({
            sessionId,
            runtimeId: item.id,
            title: item.label,
        }), { activate: true, mode: "split" });
    }, [openDocument, sessionId]);

    const openSubagentReturn = useCallback((item: SubagentReturnProjection) => {
        openDocument(createSubagentActivityDocument({
            sessionId,
            delegationId: item.delegationId || item.id,
            title: item.name,
        }), { activate: true, mode: "split" });
    }, [openDocument, sessionId]);

    const openOutput = useCallback((output: SessionOutputProjection) => {
        setFileError("");
        const artifact = output.rawArtifact ? normalizeRuntimeArtifact(output.rawArtifact) : null;
        if (artifact) {
            openDocument(createArtifactDocument(artifact, sessionId), { activate: true, mode: "split" });
            return;
        }
        if (output.path) {
            void resolveAndOpenWorkspaceFile(output.path, { sessionId }).catch((reason) => {
                setFileError(reason instanceof Error ? reason.message : String(reason));
            });
            return;
        }
    }, [openDocument, sessionId]);

    const openSource = useCallback((source: SessionSourceProjection) => {
        setFileError("");
        const workspacePath = source.workspaceRelativePath || source.workspacePath;
        if (workspacePath) {
            void resolveAndOpenWorkspaceFile(workspacePath, { sessionId }).catch((reason) => {
                setFileError(reason instanceof Error ? reason.message : String(reason));
            });
            return;
        }
        const resourceUrl = source.previewUrl || source.url;
        if (!resourceUrl) return;
        const renderer = source.mediaKind === "image"
            ? "image"
            : source.mediaKind === "video"
                ? "video"
                : source.mediaKind === "audio"
                    ? "audio"
                    : source.mimeType === "application/pdf"
                        ? "pdf"
                        : source.mimeType?.includes("markdown")
                            ? "markdown"
                            : source.mimeType?.startsWith("text/")
                                ? "text"
                                : "download";
        openDocument(createExternalArtifactDocument({
            sessionId,
            id: `source:${source.id}`,
            title: source.name,
            url: resourceUrl,
            renderer,
            mimeType: source.mimeType || undefined,
        }), { activate: true, mode: "split" });
    }, [openDocument, sessionId]);

    useEffect(() => {
        if (!sessionId) return;
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
    }, [resourceRevision, sessionId]);

    // Durable artifact/source rows can be committed between two compact
    // runtime milestones. Poll only while a run is live so the sidebar does
    // not depend on a manual page refresh, while keeping the steady-state
    // surface event-driven.
    useEffect(() => {
        if (!sessionId || !workbenchIsLive) return;
        let disposed = false;
        const refresh = () => {
            if (disposed || (typeof document !== "undefined" && document.visibilityState === "hidden")) return;
            const query = new URLSearchParams({ sessionId, limit: "100" });
            void Promise.allSettled([
                fetch(`/api/artifacts?${query.toString()}`, { cache: "no-store" }).then((response) => response.ok ? response.json() : null),
                fetch(`/api/sources?${query.toString()}`, { cache: "no-store" }).then((response) => response.ok ? response.json() : null),
            ]).then(([artifactsResult, sourcesResult]) => {
                if (disposed) return;
                if (artifactsResult.status === "fulfilled" && Array.isArray(artifactsResult.value?.artifacts)) {
                    setRuntimeArtifacts(artifactsResult.value.artifacts);
                }
                if (sourcesResult.status === "fulfilled" && Array.isArray(sourcesResult.value?.sources)) {
                    setSessionSources(sourcesResult.value.sources);
                }
            });
        };
        refresh();
        const timer = window.setInterval(refresh, 2000);
        return () => {
            disposed = true;
            window.clearInterval(timer);
        };
    }, [sessionId, workbenchIsLive]);

    useEffect(() => {
        if (!sessionId) return;
        const controller = new AbortController();
        const query = new URLSearchParams({ sessionId, limit: "100" });
        void fetch(`/api/sources?${query.toString()}`, { cache: "no-store", signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then((payload) => {
                if (!controller.signal.aborted) setSessionSources(Array.isArray(payload?.sources) ? payload.sources : []);
            })
            .catch(() => {
                if (!controller.signal.aborted) setSessionSources([]);
            });
        return () => controller.abort();
    }, [resourceRevision, sessionId]);

    return (
        <div className="h-full min-h-0 overflow-auto bg-background">
            <div className="mx-auto w-full max-w-[760px]">
            {currentRuntime || pendingConfirmation ? (
                <div className="border-b border-border/55">
                <button
                    type="button"
                    disabled={!pendingConfirmation || !onOpenPendingConfirmation}
                    onClick={pendingConfirmation ? onOpenPendingConfirmation : undefined}
                    className="flex min-h-10 w-full items-center gap-2 px-3 py-2 text-left text-[11px] disabled:cursor-default enabled:hover:bg-muted/35 enabled:focus-visible:ring-2 enabled:focus-visible:ring-inset enabled:focus-visible:ring-primary"
                >
                    <CircleDot className={`h-3 w-3 shrink-0 ${pendingConfirmation || currentRuntime?.status === "attention" ? "text-amber-500" : "text-emerald-500"}`} />
                    <span className="font-medium text-foreground">{
                        pendingConfirmation
                            ? t("web.workbench.runtime.awaiting")
                            : currentRuntime?.status === "attention"
                                ? t("web.workbench.runtime.needsAttention")
                            : t("web.workbench.runtime.running")
                    }</span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">{currentRuntime?.lastActivity || currentRuntime?.stepTitle || currentRuntime?.shortLabel || currentRuntime?.label || ""}</span>
                    {pendingConfirmation ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /> : null}
                </button>
                </div>
            ) : null}

            {visibleTodos.length ? <Section title={t("web.workbench.section.tasks")} icon={ListTodo} count={visibleTodos.length}>
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

            {runtimeActivityCards.length ? <Section title={t("web.workbench.section.runtimeActivity")} icon={Activity} count={runtimeActivityCards.length}>
                {runtimeActivityCards.map((item) => <RuntimeActivityRow key={item.id} item={item} onOpen={openRuntimeActivity} />)}
            </Section> : null}

            {subagentReturns.length ? <Section title={t("web.workbench.section.subagents")} icon={Users} count={subagentReturns.length}>
                {subagentReturns.map((item) => <SubagentReturnRow key={item.id} item={item} onOpen={openSubagentReturn} />)}
            </Section> : null}

            {outputs.length || fileError ? <Section title={t("web.workbench.section.outputs")} icon={Box} count={outputs.length}>
                {fileError ? <div className="border-b border-destructive/25 bg-destructive/5 px-3 py-2 text-[10px] text-destructive">{fileError}</div> : null}
                {visibleOutputs.map((output) => (
                    <button
                        key={output.id}
                        data-v8-context-open-workbench
                        data-session-output-row={output.id}
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
                {hiddenOutputCount > 0 ? (
                    <button
                        type="button"
                        data-artifact-disclosure="workbench"
                        aria-expanded={outputsExpanded}
                        onClick={() => setOutputsExpanded((value) => !value)}
                        className="flex h-8 w-full items-center justify-center gap-1.5 border-t border-border/30 px-3 text-[10px] font-medium text-muted-foreground hover:bg-muted/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                    >
                        {outputsExpanded
                            ? t("web.artifacts.collapse")
                            : t("web.artifacts.showRemaining", { count: hiddenOutputCount })}
                        {outputsExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    </button>
                ) : null}
            </Section> : null}

            {sources.length ? <Section title={t("web.workbench.section.sources")} icon={Paperclip} count={sources.length} defaultOpen={false}>
                {sources.map((source) => (
                    <button
                        key={source.id}
                        type="button"
                        disabled={!source.workspaceRelativePath && !source.workspacePath && !source.previewUrl && !source.url}
                        onClick={() => openSource(source)}
                        className="group flex min-h-10 w-full items-center gap-2 border-b border-border/30 px-3 py-1.5 text-left text-[11px] last:border-b-0 enabled:hover:bg-muted/40 enabled:focus-visible:ring-2 enabled:focus-visible:ring-inset enabled:focus-visible:ring-primary disabled:cursor-default"
                    >
                        <Paperclip className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium">{source.name}</span>
                            <span className="block truncate text-[10px] text-muted-foreground">
                                {source.mediaKind === "audio" ? t("web.workbench.source.audio") : source.mediaKind === "image" ? t("web.workbench.source.image") : source.mediaKind === "video" ? t("web.workbench.source.video") : t("web.workbench.source.file")}
                            </span>
                        </span>
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                    </button>
                ))}
            </Section> : null}

            {activeProcesses.length ? <Section title={t("web.workbench.section.background")} icon={TerminalSquare} count={activeProcesses.length} defaultOpen={false}>
                {activeProcesses.map((process) => (
                    <div key={process.processId} className="flex min-h-9 items-center gap-2 border-b border-border/30 px-3 py-1.5 text-[11px] last:border-b-0">
                        <TerminalSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate">{process.title || t("web.workbench.process.default")}</span>
                        <span className="text-[10px] text-muted-foreground">{t("web.workbench.process.running")}</span>
                    </div>
                ))}
            </Section> : null}

            {!hasSecondaryContent && !currentRuntime ? <EmptyRow>{todoStale ? t("web.workbench.empty.updating") : t("web.workbench.empty.default")}</EmptyRow> : null}
            </div>
        </div>
    );
}
