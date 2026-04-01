"use client";

import Link from "next/link";
import { PauseCircle, RotateCcw } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useRuntimeOpsData, formatWhen, RUN_LABELS } from "@/components/runtime/use-runtime-ops";
import { lt } from "@/lib/locale";

type RecentRunsPanelProps = {
    hook?: ReturnType<typeof useRuntimeOpsData>;
    focusRunId?: string | null;
    focusSessionId?: string | null;
};

export function RecentRunsPanel({ hook, focusRunId, focusSessionId }: RecentRunsPanelProps) {
    const t = useT();
    const defaultRuntime = useRuntimeOpsData();
    const runtime = hook ?? defaultRuntime;
    const { runs, busyKey, dispatchRunCommand } = runtime;
    const visibleRuns = runs.filter((run) => {
        if (focusRunId && run.id !== focusRunId) {
            return false;
        }
        if (focusSessionId && run.session_id !== focusSessionId) {
            return false;
        }
        return true;
    });

    const runStatusLabel = (status?: string) => {
        const normalized = String(status || "queued").trim();
        const fallback = RUN_LABELS[normalized] || normalized;
        const bilingual: Record<string, string> = {
            queued: t(lt("排队中", "Queued")),
            running: t(lt("执行中", "Running")),
            waiting_approval: t(lt("等待审批", "Waiting approval")),
            waiting_input: t(lt("等待输入", "Waiting input")),
            paused: t(lt("已暂停", "Paused")),
            completed: t(lt("已完成", "Completed")),
            failed: t(lt("失败", "Failed")),
            cancelled: t(lt("已取消", "Cancelled")),
        };
        return bilingual[normalized] || fallback;
    };

    return (
        <Card className="flex h-[520px] min-h-0 flex-col border-border/70">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <PauseCircle className="h-5 w-5 text-primary" />
                    {t(lt("最近运行", "Recent runs"))}
                </CardTitle>
                <CardDescription>
                    {t(lt("这里展示最近进入 ERC 的运行记录，可直接中断正在执行的 run，或对失败/暂停态发起重试。", "This panel shows recent ERC runs. You can interrupt active runs or retry failed and paused ones directly here."))}
                </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 space-y-3 overflow-y-auto pr-1">
                {visibleRuns.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                        {focusRunId || focusSessionId
                            ? t(lt("当前过滤条件下没有运行记录。可能该 run 已归档、列表窗口过短，或会话已经结束。", "No runs match the current filter. The run may be archived, outside the current window, or already finished."))
                            : t(lt("还没有运行记录。等下一次聊天、定时任务或审批恢复后，这里就会开始积累。", "No run records yet. They will start appearing after the next chat, automation, or approval-driven recovery."))}
                    </div>
                ) : visibleRuns.map((run) => {
                    const interruptBusy = busyKey === `interrupt:${run.id}`;
                    const retryBusy = busyKey === `retry:${run.id}`;
                    const status = run.status || "queued";
                    const taskName =
                        typeof run.metadata?.task_name === "string" && run.metadata.task_name.trim().length > 0
                            ? run.metadata.task_name
                            : null;
                    const actionTarget =
                        typeof run.metadata?.action_target === "string" && run.metadata.action_target.trim().length > 0
                            ? run.metadata.action_target
                            : null;
                    const isFocused =
                        (focusRunId && run.id === focusRunId)
                        || (focusSessionId && run.session_id === focusSessionId);
                    return (
                        <div
                            key={run.id}
                            className={`rounded-2xl border bg-background/70 p-4 shadow-sm ${
                                isFocused ? "border-sky-300 bg-sky-50/50" : "border-border/70"
                            }`}
                        >
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge>{runStatusLabel(status)}</Badge>
                                <Badge variant="outline">{run.run_type || "chat"}</Badge>
                                {run.trigger_source && <Badge variant="secondary">{run.trigger_source}</Badge>}
                            </div>
                            <div className="mt-3 space-y-1 text-sm text-muted-foreground">
                                <div>Run ID: <span className="text-foreground/90">{run.id}</span></div>
                                <div>{t(lt("会话", "Session"))}: <span className="text-foreground/90">{run.session_id || "-"}</span></div>
                                {taskName ? <div>{t(lt("任务", "Task"))}: <span className="text-foreground/90">{taskName}</span></div> : null}
                                {actionTarget ? <div>{t(lt("目标", "Target"))}: <span className="text-foreground/90 break-all">{actionTarget}</span></div> : null}
                                <div>{t(lt("时间", "Created"))}: <span className="text-foreground/90">{formatWhen(run.created_at)}</span></div>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                    <Link href={`/admin/operations-center?tab=runs&focusRun=${encodeURIComponent(run.id)}`}>
                                        {t(lt("定位当前 Run", "Locate this run"))}
                                    </Link>
                                </Button>
                                {run.session_id ? (
                                    <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                        <Link href={`/admin/operations-center?tab=approvals&focusSession=${encodeURIComponent(run.session_id)}`}>
                                            {t(lt("查看该会话确认", "View session approvals"))}
                                        </Link>
                                    </Button>
                                ) : null}
                            </div>
                            <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                                {status === "running" && (
                                    <Button
                                        type="button"
                                        variant="outline"
                                        disabled={interruptBusy || retryBusy}
                                        onClick={() => void dispatchRunCommand(run.id, "interrupt")}
                                    >
                                        <PauseCircle className="mr-2 h-4 w-4" />
                                        {interruptBusy ? t(lt("中断中...", "Interrupting...")) : t(lt("中断", "Interrupt"))}
                                    </Button>
                                )}
                                {["paused", "failed", "cancelled", "waiting_input"].includes(status) && (
                                    <Button
                                        type="button"
                                        variant="outline"
                                        disabled={interruptBusy || retryBusy}
                                        onClick={() => void dispatchRunCommand(run.id, "retry")}
                                    >
                                        <RotateCcw className="mr-2 h-4 w-4" />
                                        {retryBusy ? t(lt("重试中...", "Retrying...")) : t(lt("重试", "Retry"))}
                                    </Button>
                                )}
                            </div>
                        </div>
                    );
                })}
            </CardContent>
        </Card>
    );
}
