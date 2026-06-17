"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, ArrowRight, FileCheck2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
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
    return {
        isSpecApproval: kind === "spec_stage_approval",
        specId: readFirstFromRecord(request, "specId", "spec_id"),
        stage: readFirstFromRecord(request, "stage", "specStage", "spec_stage"),
        summary: readFirstFromRecord(request, "summary", "question", "prompt"),
        detailRef: readFirstFromRecord(request, "detailRef", "detail_ref"),
        nextStage: readFirstFromRecord(pipeline, "nextStage"),
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
                                {specDetails.isSpecApproval ? t(lt("Spec 审批", "Spec approval")) : t(lt("治理审批", "Governance approval"))}
                            </div>
                            <DialogTitle className="mt-1 text-lg font-semibold tracking-tight text-foreground">
                                {specDetails.isSpecApproval ? t(lt("Spec 阶段需要你的审阅", "Spec stage needs your review")) : t(lt("Safety Guardian 需要你的授权", "Safety Guardian needs your approval"))}
                            </DialogTitle>
                            <DialogDescription className="mt-1 text-sm leading-6 text-muted-foreground">
                                {specDetails.isSpecApproval
                                    ? t(lt("当前运行已暂停。审阅并批准本阶段后，Supervisor 会继续下一阶段。", "The current run is paused. After you review and approve this stage, Supervisor will continue the next stage."))
                                    : t(lt("当前运行已暂停，确认后会继续原命令执行。", "The current run is paused and will resume the original command after approval."))}
                            </DialogDescription>
                        </div>
                    </div>
                </DialogHeader>

                <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 sm:px-5">
                    {specDetails.isSpecApproval ? (
                        <div className="rounded-2xl border border-violet-500/25 bg-violet-50/80 p-3.5 dark:bg-violet-500/10 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-violet-700 dark:text-violet-300">
                                {t(lt("Spec 阶段", "Spec stage"))}
                            </div>
                            <div className="grid gap-2 text-xs">
                                <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                    <span className="font-semibold text-muted-foreground">stage</span>
                                    <span className="break-words text-foreground">{specDetails.stage || "-"}</span>
                                </div>
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
                            {specDetails.isSpecApproval ? t(lt("阶段摘要", "Stage summary")) : t(lt("审批原因", "Approval reason"))}
                        </div>
                        <div className="prose prose-sm max-w-none break-words text-sm leading-6 dark:prose-invert">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {(specDetails.isSpecApproval ? specDetails.summary : details.prompt) || t(lt("当前操作需要人工确认。", "This operation requires human approval."))}
                            </ReactMarkdown>
                        </div>
                    </div>

                    {specDetails.isSpecApproval ? (
                        <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t(lt("说明", "Note"))}
                            </div>
                            <div className="text-sm leading-6 text-foreground">
                                {t(lt("这是 Spec 管线门禁，不是 Safety Guardian 风险审批。批准后会恢复当前运行；拒绝会让 Supervisor 修订本阶段。", "This is a Spec pipeline gate, not a Safety Guardian risk approval. Approval resumes the current run; rejection asks Supervisor to revise this stage."))}
                            </div>
                        </div>
                    ) : details.riskSummary ? (
                        <div className="rounded-2xl border border-border/70 bg-muted/35 p-3.5 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                                {t(lt("风险摘要", "Risk summary"))}
                            </div>
                            <div className="text-sm leading-6 text-foreground">{details.riskSummary}</div>
                        </div>
                    ) : null}

                    {summaryRows.length ? (
                        <div className="rounded-2xl border border-amber-500/25 bg-amber-50/80 p-3.5 dark:bg-amber-500/10 sm:p-4">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.18em] text-amber-700 dark:text-amber-300">
                                {t(lt("事件摘要", "Event summary"))}
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
                                {t(lt("待执行命令", "Command"))}
                            </div>
                            <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-xl bg-background/80 p-3 text-xs leading-5 text-foreground">
                                {details.command}
                            </pre>
                        </div>
                    ) : null}

                    <div className="space-y-2">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                            {t(lt("补充说明", "Optional note"))}
                        </div>
                        <Textarea
                            value={answer}
                            onChange={(event) => setAnswer(event.target.value)}
                            placeholder={t(lt("可以补充授权说明，也可以直接批准。", "You can add context for the approval, or approve directly."))}
                            className="min-h-[112px] resize-none rounded-2xl border-border/70 bg-background/90 text-sm leading-6 focus-visible:ring-amber-500/30"
                        />
                    </div>
                </div>

                <DialogFooter className="border-t border-border/60 bg-background/96 px-4 py-3 sm:px-5">
                    <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-xs text-muted-foreground">
                            {t(lt("治理审批不会进入 ask_user 主线，批准后会恢复原运行。", "This governance approval is separate from ask_user; approving it resumes the original run."))}
                        </div>
                        <div className="flex flex-col-reverse gap-2 sm:flex-row">
                            <Button variant="ghost" onClick={onCancel} disabled={busy} className="rounded-xl">
                                {t(lt("稍后处理", "Dismiss"))}
                            </Button>
                            <Button variant="outline" onClick={onViewDetails} disabled={busy} className="rounded-xl">
                                {specDetails.isSpecApproval ? t(lt("打开 Spec 审批页", "Open Spec review")) : t(lt("查看详情", "View details"))}
                            </Button>
                            <Button variant="outline" onClick={() => void onReject(answer.trim())} disabled={busy} className="rounded-xl">
                                {busy ? t(lt("处理中...", "Processing...")) : t(lt("拒绝", "Reject"))}
                            </Button>
                            <Button onClick={() => void onApprove(answer.trim())} disabled={busy} className="rounded-xl">
                                <ArrowRight className="mr-2 h-4 w-4" />
                                {busy ? t(lt("处理中...", "Processing...")) : t(lt("同意并继续", "Approve and continue"))}
                            </Button>
                        </div>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
