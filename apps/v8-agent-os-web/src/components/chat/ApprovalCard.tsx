"use client";

import { AlertTriangle, ChevronDown, ShieldAlert } from "lucide-react";

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
        .filter((item): item is { key: string; value: string } => Boolean(item));
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
    // expose their full message in the disclosure instead of repeating a hint.
    void showHint;
    const styles = TONE_STYLES[tone];
    const Icon = tone === "control" ? AlertTriangle : ShieldAlert;
    const rows = summaryRows(eventSummary);
    const fullMessage = body.trim();
    const messageSummary = shortMessage(fullMessage);

    return (
        <details
            data-approval-card="compact"
            className={`group my-1 overflow-hidden rounded-xl border shadow-sm transition-shadow hover:shadow-md ${styles.wrapper}`}
        >
            <summary
                className="flex cursor-pointer list-none items-center gap-2.5 rounded-xl px-2.5 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-current [&::-webkit-details-marker]:hidden"
                title={fullMessage || title}
            >
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
                    >
                        {messageSummary}
                    </div>
                </div>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-current/60 group-open:rotate-180" aria-hidden="true" />
            </summary>
            <div className="select-text border-t border-current/10 px-3 py-2.5 text-xs leading-5">
                <p className="whitespace-pre-wrap break-words">{fullMessage || "—"}</p>
                {rows.length ? (
                    <dl className="mt-2 grid gap-1 border-t border-current/10 pt-2">
                        {rows.map((row) => (
                            <div key={row.key} className="grid grid-cols-[minmax(0,6rem)_minmax(0,1fr)] gap-2">
                                <dt className="break-words font-medium text-current/60">{row.key}</dt>
                                <dd className="whitespace-pre-wrap break-all text-current/90">{row.value}</dd>
                            </div>
                        ))}
                    </dl>
                ) : null}
            </div>
        </details>
    );
}
