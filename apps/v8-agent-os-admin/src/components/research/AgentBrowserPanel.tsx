"use client";

import { useState } from "react";
import { Globe2, Loader2, ShieldCheck } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchConfigDomain, saveConfigDomain } from "@/lib/config-registry";

type AgentBrowserResult = {
    ok: boolean;
    summary: string;
};

type SystemBaseData = {
    webFetch?: {
        useAgentBrowserProfile?: boolean;
        agentBrowserProfileAllowlist?: string[];
        [key: string]: unknown;
    };
    [key: string]: unknown;
};

const LOGIN_TARGETS = {
    metaso: { url: "https://metaso.cn/", hosts: ["metaso.cn"] },
    baidu: { url: "https://www.baidu.com/", hosts: ["baidu.com"] },
} as const;

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
    const [opening, setOpening] = useState<"generic" | keyof typeof LOGIN_TARGETS | null>(null);
    const [result, setResult] = useState<AgentBrowserResult | null>(null);

    const openAgentBrowser = async (target: "generic" | keyof typeof LOGIN_TARGETS = "generic") => {
        if (opening) return;
        setOpening(target);
        setResult(null);
        try {
            const loginTarget = target === "generic" ? null : LOGIN_TARGETS[target];
            const profileConfiguration = loginTarget ? (async () => {
                const envelope = await fetchConfigDomain<SystemBaseData>("system-base", { force: true });
                const webFetch = envelope.data?.webFetch || {};
                const allowlist = Array.from(new Set([
                    ...(Array.isArray(webFetch.agentBrowserProfileAllowlist) ? webFetch.agentBrowserProfileAllowlist : []),
                    ...loginTarget.hosts,
                ].map((host) => String(host || "").trim().toLowerCase()).filter(Boolean)));
                await saveConfigDomain<SystemBaseData>("system-base", {
                    data: {
                        ...envelope.data,
                        webFetch: {
                            ...webFetch,
                            useAgentBrowserProfile: true,
                            agentBrowserProfileAllowlist: allowlist,
                        },
                    },
                });
            })() : Promise.resolve();
            const browserRequest = fetch("/api/agent-browser/open", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: loginTarget?.url || "about:blank" }),
            });
            const [configurationResult, browserResult] = await Promise.allSettled([
                profileConfiguration,
                browserRequest,
            ]);
            if (browserResult.status === "rejected") throw browserResult.reason;
            const response = browserResult.value;
            const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
            const browserOk = response.ok && payload.ok !== false;
            const configurationOk = configurationResult.status === "fulfilled";
            setResult({
                ok: browserOk && configurationOk,
                summary: !browserOk
                    ? responseSummary(payload, t("app.admin.dashboard.research.runtime.agentBrowser.failed"))
                    : !configurationOk
                        ? t("app.admin.dashboard.research.runtime.agentBrowser.profileConfigFailed")
                        : responseSummary(payload, t("app.admin.dashboard.research.runtime.agentBrowser.opened")),
            });
        } catch (error) {
            setResult({
                ok: false,
                summary: error instanceof Error
                    ? error.message
                    : t("app.admin.dashboard.research.runtime.agentBrowser.failed"),
            });
        } finally {
            setOpening(null);
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
                <div className="flex shrink-0 flex-wrap gap-2">
                    <Button type="button" variant="outline" onClick={() => void openAgentBrowser("metaso")} disabled={Boolean(opening)}>
                        {opening === "metaso" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Globe2 className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.research.runtime.agentBrowser.openMetaso")}
                    </Button>
                    <Button type="button" variant="outline" onClick={() => void openAgentBrowser("baidu")} disabled={Boolean(opening)}>
                        {opening === "baidu" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Globe2 className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.research.runtime.agentBrowser.openBaidu")}
                    </Button>
                    <Button type="button" onClick={() => void openAgentBrowser("generic")} disabled={Boolean(opening)}>
                        {opening === "generic" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Globe2 className="mr-2 h-4 w-4" />}
                        {opening === "generic"
                            ? t("app.admin.dashboard.research.runtime.agentBrowser.opening")
                            : t("app.admin.dashboard.research.runtime.agentBrowser.open")}
                    </Button>
                </div>
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
