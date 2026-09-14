"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ExternalLink, Eye, FileText, LoaderCircle, Pencil } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import type { SessionApprovalView } from "@v8/session-realtime";
import { specError, specReviewMatches, validateRefreshedSpecApproval, verifiedSpecDocument, type SpecReviewDecision, type SpecReviewDocument } from "@/lib/spec-review";

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

function stageLabel(stage: string, t: (key: string) => string) {
    if (stage === "requirements") return t("web.specConfirmation.stage.requirements");
    if (stage === "design") return t("web.specConfirmation.stage.design");
    if (stage === "tasks") return t("web.specConfirmation.stage.tasks");
    if (stage === "bugfix") return t("web.specConfirmation.stage.bugfix");
    return t("web.specConfirmation.stage.default");
}

export function SpecDocumentConfirmationDialog({
    isOpen,
    approval,
    busy = false,
    onApprove,
    onReject,
    onViewDetails,
    onCancel,
    onReplaceApproval,
}: {
    isOpen: boolean;
    approval: SessionApprovalView;
    busy?: boolean;
    onApprove: (answer: string, review?: SpecReviewDecision) => void | Promise<void>;
    onReject: (answer: string) => void | Promise<void>;
    onViewDetails: () => void;
    onCancel: () => void;
    onReplaceApproval: (approval: SessionApprovalView, previousId: string) => void;
}) {
    const t = useT();
    const request = useMemo(() => recordOf(approval.request), [approval.request]);
    const approvalId = firstText(approval.id, approval.approval_id, approval.approvalId);
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
    const [document, setDocument] = useState<SpecReviewDocument | null>(null);
    const [savedReviewHash, setSavedReviewHash] = useState("");
    const [retainedDraft, setRetainedDraft] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const draftsRef = useRef(new Map<string, string>());
    const owner = JSON.stringify([workspacePath, specId, stage]);
    const identity = JSON.stringify([owner, approvalId, request.documentSha256]);
    const activeRef = useRef({ identity, isOpen, epoch: 0 });
    if (activeRef.current.identity !== identity || activeRef.current.isOpen !== isOpen) {
        activeRef.current = { identity, isOpen, epoch: activeRef.current.epoch + 1 };
    }
    const epoch = activeRef.current.epoch;
    const operationRef = useRef(false);
    const translateRef = useRef(t);
    translateRef.current = t;
    const stillCurrent = () => activeRef.current.epoch === epoch && activeRef.current.isOpen;

    useEffect(() => {
        if (!isOpen) return;
        const controller = new AbortController();
        setEditing(false);
        setRevisionMode(false);
        setError("");
        setDocument(null);
        setSavedReviewHash("");
        setSaving(false);
        operationRef.current = false;
        if (!approvalId || !specId || !stage || !workspacePath) {
            setLoading(false);
            setError(translateRef.current("web.specConfirmation.loadFailed"));
            return;
        }
        const load = async () => {
            setLoading(true);
            try {
                const query = new URLSearchParams({ workspace_path: workspacePath, full_content: "true" });
                const response = await fetch(`/api/specs/${encodeURIComponent(specId)}?${query.toString()}`, {
                    cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || payload.ok !== true) throw specError(payload, translateRef.current("web.specConfirmation.loadFailed"));
                const nextDocument = await verifiedSpecDocument(payload.stages?.[stage]);
                if (controller.signal.aborted) return;
                const draft = draftsRef.current.get(owner);
                setRetainedDraft(draft !== undefined && draft !== nextDocument.content ? draft : null);
                setContent(nextDocument.content);
                setSavedContent(nextDocument.content);
                setDocument(nextDocument);
            } catch (reason) {
                if (!controller.signal.aborted) {
                    setError(reason instanceof Error ? reason.message : String(reason));
                }
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        };
        void load();
        return () => { controller.abort(); };
    }, [approvalId, identity, isOpen, owner, reload, specId, stage, workspacePath]);

    const saveDocument = async () => {
        if (!document) throw new Error(t("web.specConfirmation.loadFailed"));
        if (content === savedContent) return document;
        const response = await fetch(`/api/specs/${encodeURIComponent(specId)}/stages/${encodeURIComponent(stage)}/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                workspacePath,
                action: "rewrite_stage",
                content,
                expectedDocumentSha256: document.documentSha256,
                reason: "user_reviewed_spec_document",
            }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.ok !== true) throw specError(payload, t("web.specConfirmation.saveFailed"));
        const saved = await verifiedSpecDocument(payload);
        if (saved.documentPath !== document.documentPath) throw new Error(t("web.specConfirmation.versionChanged"));
        if (!stillCurrent()) return null;
        setSavedContent(saved.content);
        setDocument(saved);
        setSavedReviewHash(saved.documentSha256);
        if (saved.content !== content) {
            setRetainedDraft(content);
            setContent(saved.content);
            setEditing(false);
            setError(t("web.specConfirmation.normalized"));
            return null;
        }
        draftsRef.current.delete(owner);
        return saved;
    };

    const approve = async () => {
        if (!document || operationRef.current || !stillCurrent()
            || (!specReviewMatches(approval, document) && savedReviewHash !== document.documentSha256)) return;
        operationRef.current = true;
        setSaving(true);
        setError("");
        try {
            const saved = await saveDocument();
            if (!saved || !stillCurrent()) return;
            await onApprove("", { approvalId, documentSha256: saved.documentSha256,
                ...(!specReviewMatches(approval, saved) ? { replaceSpecReview: true } : {}) });
        } catch (reason) {
            if (stillCurrent()) setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            if (stillCurrent()) { setSaving(false); operationRef.current = false; }
        }
    };

    const refreshReview = async () => {
        if (!document || operationRef.current) return;
        operationRef.current = true;
        setSaving(true);
        setError("");
        try {
            const response = await fetch(`/api/approvals/${encodeURIComponent(approvalId)}/refresh-spec-review`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ response: { documentSha256: document.documentSha256 } }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw specError(payload, t("web.specConfirmation.loadFailed"));
            const next = validateRefreshedSpecApproval(payload, approval, document);
            if (stillCurrent()) onReplaceApproval(next, approvalId);
        } catch (reason) {
            if (stillCurrent()) setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            if (stillCurrent()) { setSaving(false); operationRef.current = false; }
        }
    };

    const reject = async () => {
        const note = revisionNote.trim();
        if (!note || operationRef.current) return;
        operationRef.current = true;
        setSaving(true);
        setError("");
        try {
            await onReject(note);
        } catch (reason) {
            if (stillCurrent()) setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            if (stillCurrent()) { setSaving(false); operationRef.current = false; }
        }
    };

    const unavailable = loading || busy || saving;
    const needsRefresh = Boolean(document && !specReviewMatches(approval, document) && savedReviewHash !== document.documentSha256);

    return (
        <Dialog open={isOpen} onOpenChange={(open) => {
            if (!open) { activeRef.current.isOpen = false; activeRef.current.epoch += 1; onCancel(); }
        }}>
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
                            <DialogTitle className="text-base font-semibold text-foreground">{stageLabel(stage, t)}</DialogTitle>
                            <DialogDescription className="mt-1 truncate text-sm text-muted-foreground">
                                {featureName || t("web.specConfirmation.description")}
                            </DialogDescription>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                disabled={unavailable || !document}
                                onClick={() => setEditing((value) => !value)}
                                className="h-8 rounded-lg px-2.5"
                            >
                                {editing ? <Eye className="mr-1.5 h-3.5 w-3.5" /> : <Pencil className="mr-1.5 h-3.5 w-3.5" />}
                                {editing ? t("web.specConfirmation.preview") : t("web.specConfirmation.edit")}
                            </Button>
                            <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded-lg" onClick={onViewDetails} disabled={unavailable} aria-label={t("web.specConfirmation.openPage")}>
                                <ExternalLink className="h-3.5 w-3.5" />
                            </Button>
                        </div>
                    </div>
                </DialogHeader>

                <div className="min-h-0 flex-1 overflow-hidden bg-muted/15">
                    {loading ? (
                        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                            <LoaderCircle className="h-4 w-4 animate-spin" />{t("web.specConfirmation.loading")}
                        </div>
                    ) : editing ? (
                        <Textarea
                            value={content}
                            disabled={unavailable}
                            onChange={(event) => { setContent(event.target.value); draftsRef.current.set(owner, event.target.value); }}
                            className="h-full min-h-0 resize-none rounded-none border-0 bg-background px-6 py-5 text-sm leading-7 shadow-none focus-visible:ring-0"
                            aria-label={t("web.specConfirmation.editDocument")}
                        />
                    ) : (
                        <div className="scrollbar-none h-full overflow-y-auto px-6 py-5 sm:px-8">
                            <article className="prose prose-sm mx-auto max-w-[780px] break-words text-foreground dark:prose-invert prose-headings:scroll-mt-4 prose-pre:overflow-x-auto">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{document ? content : fallbackSummary || t("web.specConfirmation.empty")}</ReactMarkdown>
                            </article>
                        </div>
                    )}
                </div>

                {revisionMode ? (
                    <div className="shrink-0 border-t border-border/60 bg-background px-5 py-3">
                        <Textarea
                            value={revisionNote}
                            onChange={(event) => setRevisionNote(event.target.value)}
                            placeholder={t("web.specConfirmation.revisionPlaceholder")}
                            className="min-h-[84px] resize-none rounded-xl"
                            autoFocus
                        />
                    </div>
                ) : null}
                {error || needsRefresh ? <div role="alert" className="shrink-0 border-t border-destructive/20 bg-destructive/5 px-5 py-2 text-xs text-destructive">
                    {error || t("web.specConfirmation.versionChanged")}
                    <Button type="button" variant="ghost" size="sm" disabled={unavailable} onClick={() => setReload(value => value + 1)}>{t("web.specConfirmation.reload")}</Button>
                    {needsRefresh ? <Button type="button" variant="outline" size="sm" disabled={unavailable} onClick={() => void refreshReview()}>{t("web.specConfirmation.refreshReview")}</Button> : null}
                </div> : null}
                {retainedDraft !== null ? <div className="shrink-0 px-5 py-2 text-xs">
                    {t("web.specConfirmation.draftKept")}
                    <Button type="button" variant="ghost" size="sm" disabled={unavailable} onClick={() => {
                        setContent(retainedDraft); draftsRef.current.set(owner, retainedDraft); setRetainedDraft(null); setEditing(true);
                    }}>{t("web.specConfirmation.restoreDraft")}</Button>
                </div> : null}

                <DialogFooter className="shrink-0 border-t border-border/60 bg-background px-5 py-3 sm:justify-between sm:space-x-0">
                    <div className="text-xs text-muted-foreground">
                        {content !== savedContent ? t("web.specConfirmation.changedHint") : t("web.specConfirmation.continueHint")}
                    </div>
                    <div className="flex items-center gap-2">
                        <Button type="button" variant="ghost" onClick={onCancel} disabled={unavailable} className="rounded-lg">{t("web.specConfirmation.later")}</Button>
                        {revisionMode ? (
                            <>
                                <Button type="button" variant="ghost" onClick={() => setRevisionMode(false)} disabled={unavailable} className="rounded-lg">{t("web.specConfirmation.backDocument")}</Button>
                                <Button type="button" variant="outline" onClick={() => void reject()} disabled={unavailable || !revisionNote.trim()} className="rounded-lg">{t("web.specConfirmation.sendRevision")}</Button>
                            </>
                        ) : (
                            <Button type="button" variant="outline" onClick={() => setRevisionMode(true)} disabled={unavailable} className="rounded-lg">{t("web.specConfirmation.requestRevision")}</Button>
                        )}
                        <Button type="button" onClick={() => void approve()} disabled={unavailable || !document || needsRefresh || !content.trim()} className="rounded-lg">
                            {saving ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
                            {t(content !== savedContent ? "web.specConfirmation.saveAndApprove" : "web.specConfirmation.approve")}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
