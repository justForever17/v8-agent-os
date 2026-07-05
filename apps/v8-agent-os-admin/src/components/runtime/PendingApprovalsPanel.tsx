"use client";

import Link from "next/link";
import { AlertCircle, RefreshCw, SendHorizonal, ShieldAlert } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useRuntimeOpsData, formatWhen } from "@/components/runtime/use-runtime-ops";

type PendingApprovalsPanelProps = {
    hook?: ReturnType<typeof useRuntimeOpsData>;
    focusRunId?: string | null;
    focusSessionId?: string | null;
};

export function PendingApprovalsPanel({ hook, focusRunId, focusSessionId }: PendingApprovalsPanelProps) {
    const t = useT();
    const defaultRuntime = useRuntimeOpsData();
    const runtime = hook ?? defaultRuntime;
    const { approvalQuestions, drafts, setDrafts, loading, busyKey, refreshData, submitApproval } = runtime;
    const visibleApprovals = approvalQuestions.filter((approval) => {
        if (focusRunId && approval.run_id !== focusRunId) {
            return false;
        }
        if (focusSessionId && approval.session_id !== focusSessionId) {
            return false;
        }
        return true;
    });

    const approvalKindLabel = (kind?: string) => {
        const normalized = String(kind || "").trim().toLowerCase();
        if (normalized === "human_input_required" || normalized === "ask_user" || normalized === "waiting_input") {
            return t("components.runtime.PendingApprovalsPanel.k011bd98b");
        }
        if (normalized === "safety_review") {
            return t("components.runtime.PendingApprovalsPanel.kd9a065ff");
        }
        if (normalized === "safety_blocked") {
            return t("components.runtime.PendingApprovalsPanel.k31271875");
        }
        return normalized || t("components.runtime.PendingApprovalsPanel.ke509a1a9");
    };

    return (
        <Card className="flex h-[520px] min-h-0 flex-col border-border/70">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="flex items-center gap-2">
                        <ShieldAlert className="h-5 w-5 text-amber-500" />
                        {t("components.runtime.PendingApprovalsPanel.k61329e7f")}
                    </CardTitle>
                </div>
                <Button variant="outline" size="sm" onClick={() => void refreshData()} disabled={loading}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    {t("components.runtime.PendingApprovalsPanel.k876e8c06")}
                </Button>
            </CardHeader>
            <CardContent className="flex-1 space-y-4 overflow-y-auto pr-1">
                {visibleApprovals.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                        {focusRunId || focusSessionId
                            ? t("components.runtime.PendingApprovalsPanel.k400735c5")
                            : t("components.runtime.PendingApprovalsPanel.k74774446")}
                    </div>
                ) : visibleApprovals.map((approval) => {
                    const draft = drafts[approval.id] || "";
                    const approveBusy = busyKey === `approve:${approval.id}`;
                    const rejectBusy = busyKey === `reject:${approval.id}`;
                    const requiresAnswer = !["safety_review", "safety_blocked"].includes(approval.approval_kind || "");
                    const isFocused =
                        (focusRunId && approval.run_id === focusRunId)
                        || (focusSessionId && approval.session_id === focusSessionId);
                    return (
                        <div
                            key={approval.id}
                            className={`rounded-2xl border bg-background/70 p-4 shadow-sm ${
                                isFocused ? "border-sky-300 bg-sky-50/50" : "border-border/70"
                            }`}
                        >
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline">{approvalKindLabel(approval.approval_kind)}</Badge>
                                <Badge variant="secondary">Run {approval.run_id || "-"}</Badge>
                                <Badge variant="secondary">Session {approval.session_id || "-"}</Badge>
                            </div>
                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground/90">
                                {approval.question || t("components.runtime.PendingApprovalsPanel.k05075777")}
                            </p>
                            <div className="mt-2 text-xs text-muted-foreground">
                                {t("components.runtime.PendingApprovalsPanel.k84eb0077")} {formatWhen(approval.created_at)}
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                {approval.run_id ? (
                                    <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                        <Link href={`/admin/operations-center?tab=runs&focusRun=${encodeURIComponent(approval.run_id)}`}>
                                            {t("components.runtime.PendingApprovalsPanel.k98a50828")}
                                        </Link>
                                    </Button>
                                ) : null}
                                {approval.session_id ? (
                                    <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                        <Link href={`/admin/operations-center?tab=approvals&focusSession=${encodeURIComponent(approval.session_id)}`}>
                                            {t("components.runtime.PendingApprovalsPanel.k12d5241a")}
                                        </Link>
                                    </Button>
                                ) : null}
                            </div>
                            <Textarea
                                className="mt-3 min-h-[104px]"
                                placeholder={requiresAnswer
                                    ? t("components.runtime.PendingApprovalsPanel.k7603c939")
                                    : t("components.runtime.PendingApprovalsPanel.k05c72cdb")}
                                value={draft}
                                onChange={(event) => setDrafts((current) => ({ ...current, [approval.id]: event.target.value }))}
                            />
                            <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                                <Button
                                    type="button"
                                    variant="outline"
                                    disabled={rejectBusy || approveBusy}
                                    onClick={() => void submitApproval(approval.id, false)}
                                >
                                    <AlertCircle className="mr-2 h-4 w-4" />
                                    {rejectBusy ? t("components.runtime.PendingApprovalsPanel.kf069e51c") : t("components.runtime.PendingApprovalsPanel.k1f3d1e8d")}
                                </Button>
                                <Button
                                    type="button"
                                    disabled={approveBusy || rejectBusy || (requiresAnswer && !draft.trim())}
                                    onClick={() => void submitApproval(approval.id, true)}
                                >
                                    <SendHorizonal className="mr-2 h-4 w-4" />
                                    {approveBusy ? t("components.runtime.PendingApprovalsPanel.kfeb050f7") : t("components.runtime.PendingApprovalsPanel.kf6cf575c")}
                                </Button>
                            </div>
                        </div>
                    );
                })}
            </CardContent>
        </Card>
    );
}
