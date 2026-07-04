"use client";

import { AlertTriangle, ShieldAlert } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";

type ApprovalTone = "approval" | "safety" | "control";

type ApprovalCardProps = {
    title: string;
    body: string;
    status?: string;
    tone?: ApprovalTone;
    eventSummary?: unknown;
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

export function ApprovalCard({ title, body, status, tone = "approval", eventSummary }: ApprovalCardProps) {
    const t = useT();
    const styles = TONE_STYLES[tone];
    const Icon = tone === "control" ? AlertTriangle : ShieldAlert;
    const rows = summaryRows(eventSummary);
    const hint =
        tone === "control"
            ? t("web.generated.eab43c1dc5")
            : tone === "safety"
                ? t("web.generated.2266216d48")
            : t("web.generated.8d86b69cd1");

    return (
        <div className={`my-1.5 overflow-hidden rounded-2xl border p-3 shadow-sm ${styles.wrapper}`}>
            <div className="flex items-start gap-3">
                <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl ${styles.icon}`}>
                    <Icon className="h-4.5 w-4.5" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-[12px] font-semibold tracking-wide">{title}</strong>
                        {status ? <Badge variant="outline">{status}</Badge> : null}
                    </div>
                    <div className="mt-2 whitespace-pre-wrap text-sm leading-6">
                        {body}
                    </div>
                    {rows.length ? (
                        <div className="mt-3 grid gap-1.5 rounded-xl border border-current/15 bg-background/35 p-2.5 text-xs">
                            {rows.map((row) => (
                                <div key={row.key} className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
                                    <span className="font-semibold text-current/60">{row.key}</span>
                                    <span className="break-words text-current/90">{row.value}</span>
                                </div>
                            ))}
                        </div>
                    ) : null}
                    <div className="mt-2 text-xs font-medium text-current/70">{hint}</div>
                </div>
            </div>
        </div>
    );
}
