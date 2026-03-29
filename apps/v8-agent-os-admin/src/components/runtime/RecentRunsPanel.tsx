"use client";

import { PauseCircle, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useRuntimeOpsData, formatWhen, RUN_LABELS } from "@/components/runtime/use-runtime-ops";

type RecentRunsPanelProps = {
    hook?: ReturnType<typeof useRuntimeOpsData>;
};

export function RecentRunsPanel({ hook }: RecentRunsPanelProps) {
    const defaultRuntime = useRuntimeOpsData();
    const runtime = hook ?? defaultRuntime;
    const { runs, busyKey, dispatchRunCommand } = runtime;

    return (
        <Card className="flex h-[520px] min-h-0 flex-col border-border/70">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <PauseCircle className="h-5 w-5 text-primary" />
                    最近运行
                </CardTitle>
                <CardDescription>
                    这里展示最近进入 ERC 的运行记录，可直接中断正在执行的 run，或对失败/暂停态发起重试。
                </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 space-y-3 overflow-y-auto pr-1">
                {runs.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                        还没有运行记录。等下一次聊天、定时任务或审批恢复后，这里就会开始积累。
                    </div>
                ) : runs.map((run) => {
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
                    return (
                        <div key={run.id} className="rounded-2xl border border-border/70 bg-background/70 p-4 shadow-sm">
                            <div className="flex flex-wrap items-center gap-2">
                                <Badge>{RUN_LABELS[status] || status}</Badge>
                                <Badge variant="outline">{run.run_type || "chat"}</Badge>
                                {run.trigger_source && <Badge variant="secondary">{run.trigger_source}</Badge>}
                            </div>
                            <div className="mt-3 space-y-1 text-sm text-muted-foreground">
                                <div>Run ID: <span className="text-foreground/90">{run.id}</span></div>
                                <div>Session: <span className="text-foreground/90">{run.session_id || "-"}</span></div>
                                {taskName ? <div>任务: <span className="text-foreground/90">{taskName}</span></div> : null}
                                {actionTarget ? <div>目标: <span className="text-foreground/90 break-all">{actionTarget}</span></div> : null}
                                <div>时间: <span className="text-foreground/90">{formatWhen(run.created_at)}</span></div>
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
                                        {interruptBusy ? "中断中..." : "中断"}
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
                                        {retryBusy ? "重试中..." : "重试"}
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
