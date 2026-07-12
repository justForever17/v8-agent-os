"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { formatLocalDateTime } from "@/lib/time";
import { getRuntimeDisplayText } from "@/lib/runtime-admin";

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
    started_at?: string;
    metadata?: Record<string, unknown>;
}

export const RUN_LABELS: Record<string, string> = {
    queued: "shared.runtimeStatus.queued",
    running: "shared.runtimeStatus.running",
    waiting_approval: "shared.runtimeStatus.waitingApproval",
    waiting_input: "shared.runtimeStatus.waitingInput",
    waiting_external_tool: "shared.runtimeStatus.waitingExternalTool",
    waiting_external: "shared.runtimeStatus.waitingExternalTool",
    paused: "shared.runtimeStatus.paused",
    completed: "shared.runtimeStatus.completed",
    finished: "shared.runtimeStatus.completed",
    failed: "shared.runtimeStatus.failed",
    error: "shared.runtimeStatus.failed",
    degraded: "shared.runtimeStatus.degraded",
    blocked: "shared.runtimeStatus.blocked",
    cancelled: "shared.runtimeStatus.cancelled",
    canceled: "shared.runtimeStatus.cancelled",
};

type RuntimeStatusTranslator = (key: string) => string;

export function humanizeRuntimeStatus(status?: string) {
    const normalized = String(status || "queued").trim();
    if (!normalized) return "Queued";
    return normalized
        .replace(/[_-]+/g, " ")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/\b\w/g, (value) => value.toUpperCase());
}

export function formatRunStatusLabel(status: string | undefined, t: RuntimeStatusTranslator) {
    const normalized = String(status || "queued").trim().toLowerCase();
    const labelKey = RUN_LABELS[normalized];
    return labelKey ? t(labelKey) : humanizeRuntimeStatus(normalized);
}

export function formatRuntimeKindLabel(kind: string | undefined, t: RuntimeStatusTranslator) {
    const normalized = String(kind || "chat").trim().toLowerCase();
    const translationKey = getRuntimeDisplayText(normalized);
    const translated = t(translationKey);
    return translated && translated !== translationKey ? translated : humanizeRuntimeStatus(normalized);
}

export function formatWhen(value?: string) {
    return formatLocalDateTime(value, { includeYear: false, includeSeconds: true });
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
            "",
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
