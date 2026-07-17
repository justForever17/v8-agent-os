"use client";

import { useMemo } from "react";
import { Bot, CheckCircle2, CircleDot, GitBranch, ShieldCheck } from "lucide-react";
import {
    buildSubagentReturnProjection,
    type AdminProcessRef,
    type SubagentActivityWorkbenchDocumentRef,
    type SubagentReturnProjection,
} from "@v8/session-realtime";

import { ArtifactCard } from "@/components/chat/ArtifactCard";
import { ContentDispatcher } from "@/components/chat/ContentDispatcher";
import { ImagePreview, MediaPlayer } from "@/components/chat/MediaRenderers";
import { useT } from "@/components/providers/LocaleProvider";
import { inferArtifactCardType, normalizeRuntimeArtifact, resolveRuntimeArtifactUrl } from "@/lib/artifacts";
import type { RuntimeStageModel } from "@/lib/runtime-stage";
import { createArtifactDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";
import type { Message, UiExecutionNode, UiTimelineNode } from "@/store/chat-types";

function findProjection(items: SubagentReturnProjection[], id: string): SubagentReturnProjection | null {
    for (const item of items) {
        if (item.delegationId === id || item.id === id) return item;
        const child = findProjection(item.children, id);
        if (child) return child;
    }
    return null;
}

function statusLabel(status: string, t: ReturnType<typeof useT>) {
    const normalized = String(status || "").toLowerCase();
    if (["ok", "completed", "success", "terminated"].includes(normalized)) return t("web.workbench.subagent.status.completed");
    if (["failed", "cancelled", "degraded"].includes(normalized)) return t("web.workbench.subagent.status.attention");
    if (normalized.includes("waiting")) return t("web.workbench.subagent.status.waiting");
    return t("web.workbench.subagent.status.running");
}

function statusTone(status: string) {
    const normalized = String(status || "").toLowerCase();
    if (["ok", "completed", "success", "terminated"].includes(normalized)) return "bg-emerald-500";
    if (["failed", "cancelled", "degraded"].includes(normalized)) return "bg-rose-500";
    return "bg-violet-500";
}

function SubagentEventStream({ item, processes }: { item: SubagentReturnProjection; processes: AdminProcessRef[] }) {
    const t = useT();
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const resultByToolCall = useMemo(() => {
        const results = new Map<string, UiExecutionNode>();
        for (const event of item.events) {
            const node = event.node as UiTimelineNode;
            if (node.kind === "execution" && node.executionType === "tool_result" && node.toolCallId) {
                results.set(node.toolCallId, node);
            }
        }
        return results;
    }, [item.events]);
    const toolCalls = useMemo(() => new Set(item.events
        .map((event) => event.node as UiTimelineNode)
        .filter((node): node is UiExecutionNode => node.kind === "execution" && node.executionType === "tool_call")
        .map((node) => String(node.toolCallId || "").trim())
        .filter(Boolean)), [item.events]);

    return (
        <div className="flex flex-col gap-3">
            {item.events.map((event) => {
                const node = event.node as UiTimelineNode;
                if (!node || !["narrative", "execution", "governance", "artifact"].includes(node.kind)) return null;
                if (node.kind === "artifact") {
                    const artifact = normalizeRuntimeArtifact(node.artifact);
                    if (!artifact) return null;
                    const url = resolveRuntimeArtifactUrl(artifact);
                    const type = inferArtifactCardType(artifact);
                    if (url && type === "image") return <ImagePreview key={event.eventId} src={url} alt={artifact.displayLabel} />;
                    if (url && (type === "video" || type === "audio" || type === "music")) {
                        return <MediaPlayer key={event.eventId} src={url} type={type === "video" ? "video" : "audio"} title={artifact.displayLabel} />;
                    }
                    return (
                        <ArtifactCard
                            key={event.eventId}
                            id={artifact.id}
                            title={artifact.displayLabel}
                            type={type}
                            subtitle={artifact.displaySubtitle || t("web.workbench.subagent.artifact")}
                            onClick={() => openDocument(createArtifactDocument(artifact), { activate: true, mode: "split" })}
                            onDownload={url ? () => window.open(url, "_blank", "noopener,noreferrer") : undefined}
                        />
                    );
                }
                if (node.kind === "execution" && node.executionType === "tool_result" && node.toolCallId && toolCalls.has(node.toolCallId)) {
                    return null;
                }
                const resultNode = node.kind === "execution" && node.executionType === "tool_call" && node.toolCallId
                    ? resultByToolCall.get(node.toolCallId)
                    : undefined;
                return (
                    <div key={event.eventId} className="min-w-0" data-subagent-event-seq={event.eventSeq}>
                        <ContentDispatcher
                            node={node}
                            resultNode={resultNode}
                            isExecuting={!item.completedEventSeq && event === item.events.at(-1)}
                            isStreaming={!item.completedEventSeq && (node.kind === "narrative" || (node.kind === "execution" && node.executionType === "reasoning"))}
                            processes={processes}
                        />
                    </div>
                );
            })}
        </div>
    );
}

function SubagentSection({ item, processes, nested = false }: { item: SubagentReturnProjection; processes: AdminProcessRef[]; nested?: boolean }) {
    const t = useT();
    return (
        <section className={nested ? "mt-5 border-l border-border/60 pl-4" : ""}>
            {nested ? (
                <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-foreground">
                    <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
                    <span>{item.name}</span>
                    <span className="text-[10px] font-normal text-muted-foreground">{t("web.workbench.subagent.child")}</span>
                </div>
            ) : null}
            <SubagentEventStream item={item} processes={processes} />
            {item.summary ? (
                <div className="mt-4 rounded-xl border border-border/60 bg-muted/20 p-3">
                    <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold text-foreground"><CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />{t("web.workbench.subagent.conclusion")}</div>
                    <ContentDispatcher node={{ id: `${item.id}:summary`, kind: "narrative", role: "assistant", content: item.summary, timestamp: item.timestamp }} isExecuting={false} isStreaming={false} />
                </div>
            ) : null}
            {item.acceptanceStatus && item.acceptanceStatus !== "pending" ? (
                <div className="mt-3 flex items-start gap-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-[11px] leading-5 text-foreground/85">
                    <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                    <span>{item.acceptanceSummary || t("web.workbench.subagent.accepted")}</span>
                </div>
            ) : null}
            {item.children.map((child) => <SubagentSection key={child.id} item={child} processes={processes} nested />)}
        </section>
    );
}

export function SubagentActivityRenderer({
    document,
    messages,
    runtimeModel,
    processes,
}: {
    document: SubagentActivityWorkbenchDocumentRef;
    messages: Message[];
    runtimeModel: RuntimeStageModel;
    processes: AdminProcessRef[];
}) {
    const t = useT();
    const projections = useMemo(
        () => buildSubagentReturnProjection(messages, runtimeModel.messageActivities.map((activity) => activity.node)),
        [messages, runtimeModel.messageActivities],
    );
    const item = useMemo(
        () => findProjection(projections, document.subjectRef.delegationId),
        [document.subjectRef.delegationId, projections],
    );

    if (!item) {
        return <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">{t("web.workbench.subagent.syncing")}</div>;
    }

    return (
        <div className="h-full min-h-0 overflow-auto bg-background">
            <div className="mx-auto w-full max-w-[760px] px-4 py-4">
                <header className="mb-4 rounded-2xl border border-border/65 bg-muted/15 px-4 py-3">
                    <div className="flex items-center gap-3">
                        <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary"><Bot className="h-5 w-5" /></span>
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                                <h2 className="truncate text-sm font-semibold text-foreground">{item.name}</h2>
                                <span className="inline-flex items-center gap-1 rounded-full border border-border/65 bg-background/70 px-2 py-0.5 text-[10px] text-muted-foreground"><span className={`h-1.5 w-1.5 rounded-full ${statusTone(item.status)}`} />{statusLabel(item.status, t)}</span>
                            </div>
                            {item.taskGoal ? <p className="mt-1 line-clamp-4 break-words text-[11px] leading-5 text-muted-foreground" title={item.taskGoal}>{item.taskGoal}</p> : null}
                        </div>
                        {!item.completedEventSeq ? <CircleDot className="h-4 w-4 animate-pulse text-primary" /> : null}
                    </div>
                </header>
                <SubagentSection item={item} processes={processes} />
            </div>
        </div>
    );
}
