"use client";

import { AlertCircle, RefreshCw, SendHorizonal, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useRuntimeOpsData, formatWhen } from "@/components/runtime/use-runtime-ops";

type PendingApprovalsPanelProps = {
    hook?: ReturnType<typeof useRuntimeOpsData>;
};

export function PendingApprovalsPanel({ hook }: PendingApprovalsPanelProps) {
    const defaultRuntime = useRuntimeOpsData();
    const runtime = hook ?? defaultRuntime;
    const { approvalQuestions, drafts, setDrafts, loading, busyKey, refreshData, submitApproval } = runtime;

    return (
        <Card className="flex h-[520px] min-h-0 flex-col border-border/70">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="flex items-center gap-2">
                        <ShieldAlert className="h-5 w-5 text-amber-500" />
                        待处理审批
                    </CardTitle>
                    <CardDescription>
                        这里是 ERC 的人工介入入口。批准后会触发系统恢复，拒绝则把运行送回等待输入态。
                    </CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => void refreshData()} disabled={loading}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    刷新
                </Button>
            </CardHeader>
            <CardContent className="flex-1 space-y-4 overflow-y-auto pr-1">
                {approvalQuestions.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                        当前没有待处理审批，运行中的人工确认点已经清空。
                    </div>
                ) : approvalQuestions.map((approval) => {
                    const draft = drafts[approval.id] || "";
                    const approveBusy = busyKey === `approve:${approval.id}`;
                    const rejectBusy = busyKey === `reject:${approval.id}`;
                    const requiresAnswer = !["safety_review", "safety_blocked"].includes(approval.approval_kind || "");
                    return (
                        <div key={approval.id} className="rounded-2xl border border-border/70 bg-background/70 p-4 shadow-sm">
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline">{approval.approval_kind || "ask_user"}</Badge>
                                <Badge variant="secondary">Run {approval.run_id || "-"}</Badge>
                                <Badge variant="secondary">Session {approval.session_id || "-"}</Badge>
                            </div>
                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground/90">
                                {approval.question}
                            </p>
                            <div className="mt-2 text-xs text-muted-foreground">
                                创建于 {formatWhen(approval.created_at)}
                            </div>
                            <Textarea
                                className="mt-3 min-h-[104px]"
                                placeholder={requiresAnswer ? "输入批准说明或补充信息。批准继续时建议明确给出缺失变量或确认结论。" : "这是一条纯确认型审批。可直接批准，或补充备注后再批准。"}
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
                                    {rejectBusy ? "拒绝中..." : "拒绝"}
                                </Button>
                                <Button
                                    type="button"
                                    disabled={approveBusy || rejectBusy || (requiresAnswer && !draft.trim())}
                                    onClick={() => void submitApproval(approval.id, true)}
                                >
                                    <SendHorizonal className="mr-2 h-4 w-4" />
                                    {approveBusy ? "批准中..." : "批准继续"}
                                </Button>
                            </div>
                        </div>
                    );
                })}
            </CardContent>
        </Card>
    );
}
