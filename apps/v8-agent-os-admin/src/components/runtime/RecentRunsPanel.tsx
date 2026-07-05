"use client";

import Link from "next/link";
import { PauseCircle, RotateCcw } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useRuntimeOpsData, formatWhen, formatRunStatusLabel } from "@/components/runtime/use-runtime-ops";

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

    return (
        <Card className="flex h-[520px] min-h-0 flex-col border-border/70">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <PauseCircle className="h-5 w-5 text-primary" />
                    {t("components.runtime.RecentRunsPanel.k1a586b06")}
                </CardTitle>
            </CardHeader>
            <CardContent className="flex-1 space-y-3 overflow-y-auto pr-1">
                {visibleRuns.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                        {focusRunId || focusSessionId
                            ? t("components.runtime.RecentRunsPanel.k46bcc8f3")
                            : t("components.runtime.RecentRunsPanel.k196619f5")}
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
                                <Badge>{formatRunStatusLabel(status, t)}</Badge>
                                <Badge variant="outline">{run.run_type || "chat"}</Badge>
                                {run.trigger_source && <Badge variant="secondary">{run.trigger_source}</Badge>}
                            </div>
                            <div className="mt-3 space-y-1 text-sm text-muted-foreground">
                                <div>Run ID: <span className="text-foreground/90">{run.id}</span></div>
                                <div>{t("components.runtime.RecentRunsPanel.kdd2c7128")}: <span className="text-foreground/90">{run.session_id || "-"}</span></div>
                                {taskName ? <div>{t("components.runtime.RecentRunsPanel.k6dc0ee58")}: <span className="text-foreground/90">{taskName}</span></div> : null}
                                {actionTarget ? <div>{t("components.runtime.RecentRunsPanel.kbad18df9")}: <span className="text-foreground/90 break-all">{actionTarget}</span></div> : null}
                                <div>{t("components.runtime.RecentRunsPanel.kc585d274")}: <span className="text-foreground/90">{formatWhen(run.started_at || run.created_at)}</span></div>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                                <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                    <Link href={`/admin/operations-center?tab=runs&focusRun=${encodeURIComponent(run.id)}`}>
                                        {t("components.runtime.RecentRunsPanel.k9e801c45")}
                                    </Link>
                                </Button>
                                {run.session_id ? (
                                    <Button asChild type="button" variant="ghost" size="sm" className="h-8 rounded-xl px-3">
                                        <Link href={`/admin/operations-center?tab=approvals&focusSession=${encodeURIComponent(run.session_id)}`}>
                                            {t("components.runtime.RecentRunsPanel.k26c87004")}
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
                                        {interruptBusy ? t("components.runtime.RecentRunsPanel.k31af3bc0") : t("components.runtime.RecentRunsPanel.k3d99c2cd")}
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
                                        {retryBusy ? t("components.runtime.RecentRunsPanel.kcd92b799") : t("components.runtime.RecentRunsPanel.k3a3e39b1")}
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
