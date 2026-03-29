"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { formatLocalDateTime } from "@/lib/time";

export interface ApprovalRecord {
    id: string;
    session_id?: string;
    run_id?: string;
    approval_kind?: string;
    status?: string;
    request?: {
        question?: string;
        prompt?: string;
        toolCallId?: string;
        [key: string]: unknown;
    };
    created_at?: string;
}

export interface RunRecord {
    id: string;
    session_id?: string;
    status?: string;
    run_type?: string;
    trigger_source?: string;
    created_at?: string;
    metadata?: Record<string, unknown>;
}

export const RUN_LABELS: Record<string, string> = {
    queued: "排队中",
    running: "执行中",
    waiting_approval: "等待审批",
    waiting_input: "等待输入",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
};

export function formatWhen(value?: string) {
    return formatLocalDateTime(value, { includeYear: false, includeSeconds: true, fallback: "刚刚" });
}

export function useRuntimeOpsData() {
    const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
    const [runs, setRuns] = useState<RunRecord[]>([]);
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [busyKey, setBusyKey] = useState<string | null>(null);

    const refreshData = useCallback(async () => {
        setLoading(true);
        try {
            const [approvalRes, runRes] = await Promise.all([
                fetch("/api/approvals?status=pending", { cache: "no-store" }),
                fetch("/api/runs?limit=8", { cache: "no-store" }),
            ]);

            const approvalData = approvalRes.ok ? await approvalRes.json().catch(() => ({})) : {};
            const runData = runRes.ok ? await runRes.json().catch(() => ({})) : {};
            setApprovals(Array.isArray(approvalData?.approvals) ? approvalData.approvals : []);
            setRuns(Array.isArray(runData?.runs) ? runData.runs : []);
        } catch (error) {
            console.error("[useRuntimeOpsData] Failed to refresh runtime data:", error);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void refreshData();
        const timer = window.setInterval(() => {
            void refreshData();
        }, 15000);
        return () => window.clearInterval(timer);
    }, [refreshData]);

    const approvalQuestions = useMemo(() => approvals.map((approval) => ({
        ...approval,
        question:
            approval.request?.question ||
            approval.request?.prompt ||
            "当前审批未附带说明，请在 Engine 运行日志里进一步确认。",
    })), [approvals]);

    const submitApproval = useCallback(async (approvalId: string, approve: boolean) => {
        const responseText = drafts[approvalId]?.trim() || "";
        if (approve && !responseText) {
            return;
        }

        setBusyKey(`${approve ? "approve" : "reject"}:${approvalId}`);
        try {
            const endpoint = approve ? `/api/approvals/${approvalId}/approve` : `/api/approvals/${approvalId}/reject`;
            const response = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    response: {
                        answer: responseText,
                        approved: approve,
                    },
                }),
            });
            if (!response.ok) {
                throw new Error(`Approval action failed: ${response.status}`);
            }
            setDrafts((current) => {
                const next = { ...current };
                delete next[approvalId];
                return next;
            });
            await refreshData();
        } catch (error) {
            console.error("[useRuntimeOpsData] Failed to resolve approval:", error);
        } finally {
            setBusyKey(null);
        }
    }, [drafts, refreshData]);

    const dispatchRunCommand = useCallback(async (runId: string, command: "interrupt" | "retry") => {
        setBusyKey(`${command}:${runId}`);
        try {
            const response = await fetch(`/api/runs/${runId}/commands/${command}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    reason: command === "interrupt" ? "admin_interrupt" : "admin_retry",
                }),
            });
            if (!response.ok) {
                throw new Error(`Run command failed: ${response.status}`);
            }
            await refreshData();
        } catch (error) {
            console.error("[useRuntimeOpsData] Failed to dispatch run command:", error);
        } finally {
            setBusyKey(null);
        }
    }, [refreshData]);

    return {
        approvals,
        runs,
        approvalQuestions,
        drafts,
        setDrafts,
        loading,
        busyKey,
        refreshData,
        submitApproval,
        dispatchRunCommand,
    };
}
