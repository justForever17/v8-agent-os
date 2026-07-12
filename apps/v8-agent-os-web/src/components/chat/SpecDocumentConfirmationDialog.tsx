"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ExternalLink, Eye, FileText, LoaderCircle, Pencil } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import type { SessionApprovalView } from "@v8/session-realtime";

type SpecStagePayload = {
    content?: string;
};

type SpecDetailPayload = {
    stages?: Record<string, SpecStagePayload>;
};

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function firstText(...values: unknown[]) {
    for (const value of values) {
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    return "";
}

function errorMessage(payload: unknown, fallback: string) {
    const root = recordOf(payload);
    const detail = root.detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
    const nested = recordOf(detail);
    return firstText(nested.message, root.message, root.error) || fallback;
}

function stageLabel(stage: string) {
    if (stage === "requirements") return "确认需求";
    if (stage === "design") return "确认设计";
    if (stage === "tasks") return "确认任务";
    if (stage === "bugfix") return "确认修复说明";
    return "确认 Spec 文档";
}

export function SpecDocumentConfirmationDialog({
    isOpen,
    approval,
    busy = false,
    onApprove,
    onReject,
    onViewDetails,
    onCancel,
}: {
    isOpen: boolean;
    approval: SessionApprovalView;
    busy?: boolean;
    onApprove: (answer: string) => void | Promise<void>;
    onReject: (answer: string) => void | Promise<void>;
    onViewDetails: () => void;
    onCancel: () => void;
}) {
    const request = useMemo(() => recordOf(approval.request), [approval.request]);
    const specBrief = useMemo(() => recordOf(request.specBrief), [request.specBrief]);
    const specId = firstText(request.specId, request.spec_id);
    const stage = firstText(request.stage, request.specStage, request.spec_stage).toLowerCase();
    const workspacePath = firstText(specBrief.workspacePath, request.workspacePath, request.workspace_path);
    const featureName = firstText(specBrief.featureName, request.featureName, request.feature_name);
    const fallbackSummary = firstText(request.summary, request.question, request.prompt);
    const [content, setContent] = useState("");
    const [savedContent, setSavedContent] = useState("");
    const [editing, setEditing] = useState(false);
    const [revisionMode, setRevisionMode] = useState(false);
    const [revisionNote, setRevisionNote] = useState("");
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        if (!isOpen) return;
        let cancelled = false;
        setEditing(false);
        setRevisionMode(false);
        setRevisionNote("");
        setError("");
        if (!specId || !stage || !workspacePath) {
            setContent(fallbackSummary);
            setSavedContent(fallbackSummary);
            return;
        }
        const load = async () => {
            setLoading(true);
            try {
                const query = new URLSearchParams({ workspace_path: workspacePath, max_chars: "160000" });
                const response = await fetch(`/api/specs/${encodeURIComponent(specId)}?${query.toString()}`, { cache: "no-store" });
                const payload = await response.json().catch(() => ({})) as SpecDetailPayload;
                if (!response.ok) throw new Error(errorMessage(payload, "无法读取 Spec 文档。"));
                if (cancelled) return;
                const nextContent = String(payload.stages?.[stage]?.content || fallbackSummary || "").trim();
                setContent(nextContent);
                setSavedContent(nextContent);
            } catch (reason) {
                if (!cancelled) {
                    setError(reason instanceof Error ? reason.message : String(reason));
                    setContent(fallbackSummary);
                    setSavedContent(fallbackSummary);
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [fallbackSummary, isOpen, specId, stage, workspacePath]);

    const saveDocument = async () => {
        if (!specId || !stage || !workspacePath || content === savedContent) return;
        const response = await fetch(`/api/specs/${encodeURIComponent(specId)}/stages/${encodeURIComponent(stage)}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                workspacePath,
                action: "rewrite_stage",
                content,
                reason: "user_reviewed_spec_document",
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(errorMessage(payload, "保存 Spec 文档失败。"));
        setSavedContent(content);
    };

    const approve = async () => {
        setSaving(true);
        setError("");
        try {
            await saveDocument();
            await onApprove("");
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSaving(false);
        }
    };

    const reject = async () => {
        const note = revisionNote.trim();
        if (!note) return;
        setSaving(true);
        setError("");
        try {
            await onReject(note);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSaving(false);
        }
    };

    const unavailable = loading || busy || saving;

    return (
        <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onCancel(); }}>
            <DialogContent
                showCloseButton={false}
                overlayClassName="bg-black/45 backdrop-blur-[1px]"
                className="flex h-[min(92dvh,840px)] w-[min(96vw,980px)] max-w-none flex-col gap-0 overflow-hidden border-border/80 bg-background p-0 shadow-xl sm:rounded-2xl"
            >
                <DialogHeader className="shrink-0 border-b border-border/60 px-5 py-4 text-left">
                    <div className="flex items-start gap-3">
                        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                            <FileText className="h-4 w-4" />
                        </div>
                        <div className="min-w-0 flex-1">
                            <DialogTitle className="text-base font-semibold text-foreground">{stageLabel(stage)}</DialogTitle>
                            <DialogDescription className="mt-1 truncate text-sm text-muted-foreground">
                                {featureName || "请检查文档是否准确表达了你的要求。"}
                            </DialogDescription>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                disabled={unavailable}
                                onClick={() => setEditing((value) => !value)}
                                className="h-8 rounded-lg px-2.5"
                            >
                                {editing ? <Eye className="mr-1.5 h-3.5 w-3.5" /> : <Pencil className="mr-1.5 h-3.5 w-3.5" />}
                                {editing ? "预览" : "编辑"}
                            </Button>
                            <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded-lg" onClick={onViewDetails} disabled={unavailable} aria-label="在独立页面打开">
                                <ExternalLink className="h-3.5 w-3.5" />
                            </Button>
                        </div>
                    </div>
                </DialogHeader>

                <div className="min-h-0 flex-1 overflow-hidden bg-muted/15">
                    {loading ? (
                        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                            <LoaderCircle className="h-4 w-4 animate-spin" />正在读取文档…
                        </div>
                    ) : editing ? (
                        <Textarea
                            value={content}
                            onChange={(event) => setContent(event.target.value)}
                            className="h-full min-h-0 resize-none rounded-none border-0 bg-background px-6 py-5 text-sm leading-7 shadow-none focus-visible:ring-0"
                            aria-label="编辑 Spec 文档"
                        />
                    ) : (
                        <div className="scrollbar-none h-full overflow-y-auto px-6 py-5 sm:px-8">
                            <article className="prose prose-sm mx-auto max-w-[780px] break-words text-foreground dark:prose-invert prose-headings:scroll-mt-4 prose-pre:overflow-x-auto">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || "文档内容为空。"}</ReactMarkdown>
                            </article>
                        </div>
                    )}
                </div>

                {revisionMode ? (
                    <div className="shrink-0 border-t border-border/60 bg-background px-5 py-3">
                        <Textarea
                            value={revisionNote}
                            onChange={(event) => setRevisionNote(event.target.value)}
                            placeholder="指出需要修改的内容，Agent 会据此更新文档。"
                            className="min-h-[84px] resize-none rounded-xl"
                            autoFocus
                        />
                    </div>
                ) : null}
                {error ? <div className="shrink-0 border-t border-destructive/20 bg-destructive/5 px-5 py-2 text-xs text-destructive">{error}</div> : null}

                <DialogFooter className="shrink-0 border-t border-border/60 bg-background px-5 py-3 sm:justify-between sm:space-x-0">
                    <div className="text-xs text-muted-foreground">
                        {content !== savedContent ? "文档已修改，同意时会先保存。" : "确认后 Agent 会继续下一阶段。"}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button type="button" variant="ghost" onClick={onCancel} disabled={unavailable} className="rounded-lg">稍后</Button>
                        {revisionMode ? (
                            <>
                                <Button type="button" variant="ghost" onClick={() => setRevisionMode(false)} disabled={unavailable} className="rounded-lg">返回文档</Button>
                                <Button type="button" variant="outline" onClick={() => void reject()} disabled={unavailable || !revisionNote.trim()} className="rounded-lg">发送修改意见</Button>
                            </>
                        ) : (
                            <Button type="button" variant="outline" onClick={() => setRevisionMode(true)} disabled={unavailable} className="rounded-lg">需要修改</Button>
                        )}
                        <Button type="button" onClick={() => void approve()} disabled={unavailable || !content.trim()} className="rounded-lg">
                            {saving ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
                            同意并继续
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
