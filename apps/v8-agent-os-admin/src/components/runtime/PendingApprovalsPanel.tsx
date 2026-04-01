"use client";

import Link from "next/link";
import { AlertCircle, RefreshCw, SendHorizonal, ShieldAlert } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useRuntimeOpsData, formatWhen } from "@/components/runtime/use-runtime-ops";
import { lt } from "@/lib/locale";

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
            return t(lt("等待人工输入", "Human input required"));
        }
        if (normalized === "safety_review") {
            return t(lt("安全复核", "Safety review"));
        }
        if (normalized === "safety_blocked") {
            return t(lt("安全阻断", "Safety blocked"));
        }
        return normalized || t(lt("人工确认", "Approval"));
    };

    return (
        <Card className="flex h-[520px] min-h-0 flex-col border-border/70">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="flex items-center gap-2">
                        <ShieldAlert className="h-5 w-5 text-amber-500" />
                        {t(lt("待处理确认", "Pending approvals"))}
                    </CardTitle>
                    <CardDescription>
                        {t(lt("这里是 ERC 的人工介入入口。批准后会触发系统恢复，拒绝则会把运行退回等待输入或安全阻断态。", "This is the ERC entry point for human intervention. Approving resumes the run, while rejecting sends it back to waiting-input or blocked safety state."))}
                    </CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => void refreshData()} disabled={loading}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    {t(lt("刷新", "Refresh"))}
                </Button>
            </CardHeader>
            <CardContent className="flex-1 space-y-4 overflow-y-auto pr-1">
                {visibleApprovals.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                        {focusRunId || focusSessionId
                            ? t(lt("当前过滤条件下没有待处理确认。你可以切回总览，或检查对应 run / session 是否已经恢复。", "No pending approvals match the current filter. Switch back to the full list or check whether the run / session has already resumed."))
                            : t(lt("当前没有待处理确认，运行中的人工介入点已经清空。", "There are no pending approvals right now."))}
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
                                {approval.question || t(lt("当前审批未附带说明，请在 Engine 运行日志里进一步确认。", "This approval does not include a question yet. Check the Engine runtime logs for more detail."))}
                            </p>
                            <div className="mt-2 text-xs text-muted-foreground">
                                {t(lt("创建于", "Created"))} {formatWhen(approval.created_at)}
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                {approval.run_id ? (
                                    <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                        <Link href={`/admin/operations-center?tab=runs&focusRun=${encodeURIComponent(approval.run_id)}`}>
                                            {t(lt("定位 Run", "Locate run"))}
                                        </Link>
                                    </Button>
                                ) : null}
                                {approval.session_id ? (
                                    <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                        <Link href={`/admin/operations-center?tab=approvals&focusSession=${encodeURIComponent(approval.session_id)}`}>
                                            {t(lt("按会话筛选", "Filter by session"))}
                                        </Link>
                                    </Button>
                                ) : null}
                            </div>
                            <Textarea
                                className="mt-3 min-h-[104px]"
                                placeholder={requiresAnswer
                                    ? t(lt("输入批准说明或补充信息。批准继续时建议明确给出缺失变量或确认结论。", "Enter the answer or missing information needed to continue."))
                                    : t(lt("这是一条纯确认型审批。可直接批准，或补充备注后再批准。", "This is a confirmation-style approval. You can approve directly or add notes first."))}
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
                                    {rejectBusy ? t(lt("拒绝中...", "Rejecting...")) : t(lt("拒绝", "Reject"))}
                                </Button>
                                <Button
                                    type="button"
                                    disabled={approveBusy || rejectBusy || (requiresAnswer && !draft.trim())}
                                    onClick={() => void submitApproval(approval.id, true)}
                                >
                                    <SendHorizonal className="mr-2 h-4 w-4" />
                                    {approveBusy ? t(lt("批准中...", "Approving...")) : t(lt("批准继续", "Approve and continue"))}
                                </Button>
                            </div>
                        </div>
                    );
                })}
            </CardContent>
        </Card>
    );
}
