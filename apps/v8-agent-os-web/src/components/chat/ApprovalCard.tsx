"use client";

import { AlertTriangle, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";

type ApprovalTone = "approval" | "safety" | "control";

type ApprovalCardProps = {
    title: string;
    body: string;
    status?: string;
    tone?: ApprovalTone;
    eventSummary?: unknown;
    showIcon?: boolean;
    showStatus?: boolean;
    showHint?: boolean;
};

const TONE_STYLES: Record<ApprovalTone, { wrapper: string; icon: string }> = {
    approval: {
        wrapper: "border-amber-300/60 bg-amber-50/80 text-amber-950 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100",
        icon: "bg-amber-500/12 text-amber-600 dark:text-amber-300",
    },
    safety: {
        wrapper: "border-rose-300/60 bg-rose-50/80 text-rose-950 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-100",
        icon: "bg-rose-500/12 text-rose-600 dark:text-rose-300",
    },
    control: {
        wrapper: "border-slate-300/70 bg-slate-50/90 text-slate-900 dark:border-slate-600/60 dark:bg-slate-800/70 dark:text-slate-100",
        icon: "bg-slate-500/12 text-slate-600 dark:text-slate-300",
    },
};

function asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

function summaryRows(value: unknown) {
    const summary = asRecord(value);
    if (!summary) return [];
    const keys = ["operation", "target", "host", "providerId", "credentialClass", "riskCode", "matchedRule", "nextAction"];
    return keys
        .map((key) => {
            const item = summary[key];
            return typeof item === "string" && item.trim()
                ? { key, value: item.trim() }
                : null;
        })
        .filter((item): item is { key: string; value: string } => Boolean(item))
        .slice(0, 6);
}

function shortMessage(value: string) {
    const firstLine = value.split(/\r?\n/, 1)[0]?.trim() || value.trim();
    return firstLine || "—";
}

export function ApprovalCard({
    title,
    body,
    status,
    tone = "approval",
    eventSummary,
    showIcon = true,
    showStatus = true,
    showHint = true,
}: ApprovalCardProps) {
    // Kept for callers that still pass the legacy hint flag. Generic cards now
    // keep the explanation in the hoverable full message instead of repeating it.
    void showHint;
    const styles = TONE_STYLES[tone];
    const Icon = tone === "control" ? AlertTriangle : ShieldAlert;
    const rows = summaryRows(eventSummary);
    const fullMessage = body.trim();
    const messageSummary = shortMessage(fullMessage);

    return (
        <div
            data-approval-card="compact"
            className={`my-1 overflow-hidden rounded-xl border px-2.5 py-2 shadow-sm transition-shadow hover:shadow-md ${styles.wrapper}`}
            title={fullMessage || title}
        >
            <div className="flex items-center gap-2.5">
                {showIcon ? (
                    <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${styles.icon}`} aria-hidden="true">
                        <Icon className="h-3.5 w-3.5" />
                    </div>
                ) : null}
                <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-1.5">
                        <strong className="truncate text-[11px] font-semibold tracking-wide" title={title}>{title}</strong>
                        {showStatus && status ? <Badge variant="outline" className="h-5 shrink-0 rounded-md px-1.5 text-[10px] font-medium">{status}</Badge> : null}
                    </div>
                    <div
                        className="mt-0.5 truncate text-xs leading-5 text-current/80"
                        title={fullMessage || undefined}
                        aria-label={fullMessage || undefined}
                    >
                        {messageSummary}
                    </div>
                    {rows.length ? (
                        <div className="mt-1.5 flex min-w-0 flex-wrap gap-1" aria-label="Approval details">
                            {rows.map((row) => (
                                <div key={row.key} className="max-w-full rounded-md border border-current/15 bg-background/35 px-1.5 py-0.5 text-[10px] leading-4" title={`${row.key}: ${row.value}`}>
                                    <span className="font-semibold text-current/55">{row.key}</span>
                                    <span className="ml-1 truncate text-current/85">{row.value}</span>
                                </div>
                            ))}
                        </div>
                    ) : null}
                </div>
            </div>
        </div>
    );
}
