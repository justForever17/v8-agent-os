"use client";

import { useState } from "react";
import { Globe2, Loader2, ShieldCheck } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

type AgentBrowserResult = {
    ok: boolean;
    summary: string;
};

function responseSummary(payload: Record<string, unknown>, fallback: string) {
    const summary = typeof payload.summary === "string" && payload.summary.trim()
        ? payload.summary.trim()
        : typeof payload.detail === "string" && payload.detail.trim()
            ? payload.detail.trim()
            : "";
    const nextAction = payload.ok === false
        && typeof payload.recommendedNextAction === "string"
        && payload.recommendedNextAction.trim()
        ? payload.recommendedNextAction.trim()
        : "";
    if (summary) return nextAction && nextAction !== summary ? `${summary} ${nextAction}` : summary;
    if (payload.detail && typeof payload.detail === "object" && !Array.isArray(payload.detail)) {
        const message = (payload.detail as Record<string, unknown>).message;
        if (typeof message === "string" && message.trim()) return message.trim();
    }
    return nextAction || fallback;
}

export function AgentBrowserPanel() {
    const t = useT();
    const [opening, setOpening] = useState(false);
    const [result, setResult] = useState<AgentBrowserResult | null>(null);

    const openAgentBrowser = async () => {
        if (opening) return;
        setOpening(true);
        setResult(null);
        try {
            const response = await fetch("/api/agent-browser/open", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: "about:blank" }),
            });
            const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
            setResult({
                ok: response.ok && payload.ok !== false,
                summary: responseSummary(
                    payload,
                    response.ok
                        ? t("app.admin.dashboard.research.runtime.agentBrowser.opened")
                        : t("app.admin.dashboard.research.runtime.agentBrowser.failed"),
                ),
            });
        } catch (error) {
            setResult({
                ok: false,
                summary: error instanceof Error
                    ? error.message
                    : t("app.admin.dashboard.research.runtime.agentBrowser.failed"),
            });
        } finally {
            setOpening(false);
        }
    };

    return (
        <Card data-agent-browser-panel className="rounded-3xl border-border bg-card/95 shadow-sm">
            <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/25 dark:bg-sky-500/10 dark:text-sky-300">
                        <Globe2 className="h-5 w-5" />
                    </div>
                    <div className="min-w-0">
                        <div className="text-base font-semibold text-foreground">
                            {t("app.admin.dashboard.research.runtime.agentBrowser.title")}
                        </div>
                        <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
                            {t("app.admin.dashboard.research.runtime.agentBrowser.description")}
                        </p>
                        <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                            <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                            {t("app.admin.dashboard.research.runtime.agentBrowser.privacy")}
                        </div>
                    </div>
                </div>
                <Button type="button" onClick={() => void openAgentBrowser()} disabled={opening} className="shrink-0">
                    {opening ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Globe2 className="mr-2 h-4 w-4" />}
                    {opening
                        ? t("app.admin.dashboard.research.runtime.agentBrowser.opening")
                        : t("app.admin.dashboard.research.runtime.agentBrowser.open")}
                </Button>
            </CardContent>
            {result ? (
                <div className={`border-t px-5 py-3 text-xs leading-5 ${result.ok
                    ? "border-emerald-200 bg-emerald-50/70 text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200"
                    : "border-rose-200 bg-rose-50/70 text-rose-800 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-200"}`}>
                    {result.summary}
                </div>
            ) : null}
        </Card>
    );
}
