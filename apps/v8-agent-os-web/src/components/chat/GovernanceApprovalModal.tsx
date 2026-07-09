"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, ArrowRight, FileCheck2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import type { SessionApprovalView } from "@v8/session-realtime";

function readFirstString(...values: unknown[]) {
    for (const value of values) {
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return "";
}

function stringifyCommand(value: unknown) {
    if (typeof value === "string" && value.trim()) {
        return value.trim();
    }
    if (value && typeof value === "object") {
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return "";
        }
    }
    return "";
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function extractEventSummary(request: Record<string, unknown>, safety: Record<string, unknown>) {
    const direct = asRecord(request.eventSummary);
    if (Object.keys(direct).length) return direct;
    const safetySummary = asRecord(safety.eventSummary);
    if (Object.keys(safetySummary).length) return safetySummary;
    const details = asRecord(safety.details);
    const nested = asRecord(details.eventSummary);
    return Object.keys(nested).length ? nested : {};
}

function eventSummaryRows(summary: Record<string, unknown>) {
    const keys = ["operation", "target", "host", "providerId", "credentialClass", "riskCode", "matchedRule", "nextAction"];
    return keys
        .map((key) => {
            const value = summary[key];
            return typeof value === "string" && value.trim() ? { key, value: value.trim() } : null;
        })
        .filter((item): item is { key: string; value: string } => Boolean(item))
        .slice(0, 8);
}

function extractApprovalDetails(approval: SessionApprovalView) {
    const request = approval.request && typeof approval.request === "object"
        ? approval.request as Record<string, unknown>
        : {};
    const safety = request.safety && typeof request.safety === "object"
        ? request.safety as Record<string, unknown>
        : {};
    const prompt = readFirstString(request.prompt, request.question);
    const riskSummary = readFirstString(
        safety.riskSummary,
        safety.risk_summary,
        safety.reason,
        request.reason,
        request.summary,
    );
    const command = stringifyCommand(
        safety.command
        ?? request.command
        ?? request.args
        ?? request.payload,
    );

    return {
        prompt,
        riskSummary,
        command,
        eventSummary: extractEventSummary(request, safety),
    };
}

function readFirstFromRecord(record: Record<string, unknown>, ...keys: string[]) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return "";
}

function readArray(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

function summarizeChecklist(specBrief: Record<string, unknown>) {
    const qualityEvidence = asRecord(specBrief.qualityEvidence);
    const checklists = asRecord(qualityEvidence.checklists);
    const requirements = asRecord(checklists.requirements);
    const unresolved = Number(requirements.unresolvedCount ?? 0) || 0;
    return {
        title: readFirstFromRecord(requirements, "title") || "Requirements checklist",
        status: readFirstFromRecord(requirements, "status"),
        unresolved,
        detailRef: readFirstFromRecord(requirements, "detailRef"),
    };
}

function summarizeSpecAnalysis(analysis: Record<string, unknown>) {
    return {
        blockers: readArray(analysis.hardBlockers)
            .map((item) => typeof item === "string" ? item : JSON.stringify(item))
            .slice(0, 4),
        warnings: readArray(analysis.warnings)
            .map((item) => typeof item === "string" ? item : JSON.stringify(item))
            .slice(0, 4),
    };
}

function extractSpecApprovalDetails(approval: SessionApprovalView | null) {
    const request = approval?.request && typeof approval.request === "object"
        ? approval.request as Record<string, unknown>
        : {};
    const kind = String(
        approval?.approval_kind
        || request.approvalKind
        || request.approval_kind
        || "",
    ).trim().toLowerCase();
    const pipeline = request.pipelineControl && typeof request.pipelineControl === "object"
        ? request.pipelineControl as Record<string, unknown>
        : {};
    const specBrief = asRecord(request.specBrief);
    const analysis = asRecord(request.analysis);
    const clarification = asRecord(specBrief.clarificationSummary);
    const checklist = summarizeChecklist(specBrief);
    const analysisSummary = summarizeSpecAnalysis(analysis);
    return {
        isSpecApproval: kind === "spec_stage_approval",
        featureName: readFirstFromRecord(specBrief, "featureName") || readFirstFromRecord(request, "featureName", "feature_name"),
        specId: readFirstFromRecord(request, "specId", "spec_id"),
        stage: readFirstFromRecord(request, "stage", "specStage", "spec_stage"),
        summary: readFirstFromRecord(request, "summary", "question", "prompt"),
        detailRef: readFirstFromRecord(request, "detailRef", "detail_ref"),
        workspacePath: readFirstFromRecord(specBrief, "workspacePath") || readFirstFromRecord(request, "workspacePath", "workspace_path"),
        nextStage: readFirstFromRecord(pipeline, "nextStage"),
        checklist,
        analysisSummary,
        clarificationCount: Number(clarification.count ?? 0) || 0,
        latestClarification: readFirstString(clarification.latestSummary),
    };
}

export function GovernanceApprovalModal({
    isOpen,
    approval,
    busy = false,
    onApprove,
    onReject,
    onViewDetails,
    onCancel,
}: {
    isOpen: boolean;
    approval: SessionApprovalView | null;
    busy?: boolean;
    onApprove: (answer: string) => void | Promise<void>;
    onReject: (answer: string) => void | Promise<void>;
    onViewDetails: () => void;
    onCancel: () => void;
}) {
    const t = useT();
    const [answer, setAnswer] = useState("");
    const details = useMemo(() => approval ? extractApprovalDetails(approval) : null, [approval]);
    const specDetails = useMemo(() => extractSpecApprovalDetails(approval), [approval]);
    const summaryRows = useMemo(() => details ? eventSummaryRows(details.eventSummary) : [], [details]);

    if (!approval || !details) {
        return null;
    }

    return (
        <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onCancel(); }}>
            <DialogContent className="max-h-[min(88vh,680px)] w-[min(94vw,620px)] overflow-hidden border border-amber-500/20 bg-background/96 p-0 shadow-2xl backdrop-blur-2xl sm:rounded-3xl">
                <DialogHeader className="border-b border-border/60 px-4 pb-3 pt-4 sm:px-5">
                    <div className="flex items-start gap-3">
                            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl ${specDetails.isSpecApproval ? "bg-violet-500/10 text-violet-600 dark:text-violet-300" : "bg-amber-500/10 text-amber-600 dark:text-amber-300"}`}>
                            {specDetails.isSpecApproval ? <FileCheck2 className="h-5 w-5" /> : <ShieldAlert className="h-5 w-5" />}
                        </div>
                        <div className="min-w-0 flex-1">
                            <div className={`flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.18em] ${specDetails.isSpecApproval ? "text-violet-600/85 dark:text-violet-300/85" : "text-amber-600/85 dark:text-amber-300/85"}`}>
                                <AlertTriangle className="h-3.5 w-3.5" />
                                {specDetails.isSpecApproval ? t("web.generated.bf3278f529") : t("web.generated.53031cafe1")}
                            </div>
                            <DialogTitle className="mt-1 text-lg font-semibold tracking-tight text-foreground">
                                {specDetails.isSpecApproval ? t("web.generated.21a629136d") : t("web.generated.bbd4ff80ad")}
                            </DialogTitle>
                            <DialogDescription className="mt-1 text-sm leading-6 text-muted-foreground">
                                {specDetails.isSpecApproval
                                    ? t("web.generated.624eb0f255")
                                    : t("web.generated.29b7f7031b")}
                            </DialogDescription>
                        </div>
                    </div>
                </DialogHeader>

                <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 sm:px-5">
                    {specDetails.isSpecApproval ? (
                        <div className="rounded-2xl border border-violet-500/25 bg-violet-50/80 p-3.5 dark:bg-violet-500/10 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-violet-700 dark:text-violet-300">
                                {t("web.generated.7e9f25f8c7")}
                            </div>
                                <div className="grid gap-2 text-xs">
                                {specDetails.featureName ? (
                                    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                        <span className="font-semibold text-muted-foreground">feature</span>
                                        <span className="break-words text-foreground">{specDetails.featureName}</span>
                                    </div>
                                ) : null}
                                <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                    <span className="font-semibold text-muted-foreground">stage</span>
                                    <span className="break-words text-foreground">{specDetails.stage || "-"}</span>
                                </div>
                                {specDetails.workspacePath ? (
                                    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                        <span className="font-semibold text-muted-foreground">workspace</span>
                                        <span className="break-words text-foreground">{specDetails.workspacePath}</span>
                                    </div>
                                ) : null}
                                <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                    <span className="font-semibold text-muted-foreground">specId</span>
                                    <span className="break-words text-foreground">{specDetails.specId || "-"}</span>
                                </div>
                                {specDetails.nextStage ? (
                                    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                        <span className="font-semibold text-muted-foreground">next</span>
                                        <span className="break-words text-foreground">{specDetails.nextStage}</span>
                                    </div>
                                ) : null}
                                {specDetails.detailRef ? (
                                    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                        <span className="font-semibold text-muted-foreground">detail</span>
                                        <span className="break-words text-foreground">{specDetails.detailRef}</span>
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    ) : null}
                    <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                        <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                            {specDetails.isSpecApproval ? t("web.generated.af691ef7d3") : t("web.generated.530137dea5")}
                        </div>
                        <div className="prose prose-sm max-w-none break-words text-sm leading-6 dark:prose-invert">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {(specDetails.isSpecApproval ? specDetails.summary : details.prompt) || t("web.generated.38a50d64dd")}
                            </ReactMarkdown>
                        </div>
                    </div>

                    {specDetails.isSpecApproval ? (
                        <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t("web.generated.2448f8454e")}
                            </div>
                            <div className="space-y-2 text-sm leading-6 text-foreground">
                                <div>
                                    {specDetails.checklist.title}: {specDetails.checklist.status || "pending"}
                                    {specDetails.checklist.unresolved > 0 ? `，未完成 ${specDetails.checklist.unresolved} 项` : ""}
                                </div>
                                {specDetails.clarificationCount > 0 ? (
                                    <div>澄清记录：{specDetails.clarificationCount} 条{specDetails.latestClarification ? `，最近：${specDetails.latestClarification}` : ""}</div>
                                ) : (
                                    <div>澄清记录：暂无。</div>
                                )}
                                {specDetails.analysisSummary.blockers.length > 0 ? (
                                    <div className="text-red-600 dark:text-red-300">
                                        阻断：{specDetails.analysisSummary.blockers.join("；")}
                                    </div>
                                ) : null}
                                {specDetails.analysisSummary.warnings.length > 0 ? (
                                    <div className="text-amber-700 dark:text-amber-300">
                                        提醒：{specDetails.analysisSummary.warnings.join("；")}
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    ) : details.riskSummary ? (
                        <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t("web.generated.71323a8286")}
                            </div>
                            <div className="text-sm leading-6 text-foreground">{details.riskSummary}</div>
                        </div>
                    ) : null}

                    {summaryRows.length ? (
                        <div className="rounded-2xl border border-amber-500/25 bg-amber-50/80 p-3.5 dark:bg-amber-500/10 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-amber-700 dark:text-amber-300">
                                {t("web.generated.182ad9c0f3")}
                            </div>
                            <div className="grid gap-2 text-xs">
                                {summaryRows.map((row) => (
                                    <div key={row.key} className="grid grid-cols-[116px_minmax(0,1fr)] gap-2">
                                        <span className="font-semibold text-muted-foreground">{row.key}</span>
                                        <span className="break-words text-foreground">{row.value}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    ) : null}

                    {details.command ? (
                        <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t("web.generated.4483480bb4")}
                            </div>
                            <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-xl bg-background/80 p-3 text-xs leading-5 text-foreground">
                                {details.command}
                            </pre>
                        </div>
                    ) : null}

                    <div className="space-y-2">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                            {t("web.generated.85a793722f")}
                        </div>
                        <Textarea
                            value={answer}
                            onChange={(event) => setAnswer(event.target.value)}
                            placeholder={t("web.generated.e859f34e08")}
                            className="min-h-[112px] resize-none rounded-2xl border-border/70 bg-background/90 text-sm leading-6 focus-visible:ring-amber-500/30"
                        />
                    </div>
                </div>

                <DialogFooter className="border-t border-border/60 bg-background/96 px-4 py-3 sm:px-5">
                    <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-xs text-muted-foreground">
                            {t("web.generated.194c543166")}
                        </div>
                        <div className="flex flex-col-reverse gap-2 sm:flex-row">
                            <Button variant="ghost" onClick={onCancel} disabled={busy} className="rounded-xl">
                                {t("web.generated.58c5a6eeb9")}
                            </Button>
                            <Button variant="outline" onClick={onViewDetails} disabled={busy} className="rounded-xl">
                                {specDetails.isSpecApproval ? t("web.generated.71055e26d6") : t("web.generated.b34027e86b")}
                            </Button>
                            <Button variant="outline" onClick={() => void onReject(answer.trim())} disabled={busy} className="rounded-xl">
                                {busy ? t("web.generated.0a921f2e7e") : t("web.generated.39589b7736")}
                            </Button>
                            <Button onClick={() => void onApprove(answer.trim())} disabled={busy} className="rounded-xl">
                                <ArrowRight className="mr-2 h-4 w-4" />
                                {busy ? t("web.generated.0a921f2e7e") : t("web.generated.9580a43e18")}
                            </Button>
                        </div>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
