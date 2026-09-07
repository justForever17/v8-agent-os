"use client";

import { useState } from "react";
import { Globe2, Loader2, ShieldCheck } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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

export function parseAgentBrowserLoginTarget(value: string) {
    const url = new URL(value.trim());
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname
        || url.username || url.password || url.hostname.includes("*")) {
        throw new Error("invalid_login_site");
    }
    return { url: url.href, hosts: [url.hostname.toLowerCase()] };
}

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
    const [siteUrl, setSiteUrl] = useState("");
    const [result, setResult] = useState<AgentBrowserResult | null>(null);

    const openAgentBrowser = async () => {
        if (opening) return;
        let loginTarget: { url: string; hosts: string[] };
        try {
            loginTarget = parseAgentBrowserLoginTarget(siteUrl);
        } catch {
            setResult({ ok: false, summary: t("app.admin.dashboard.research.runtime.agentBrowser.invalidSite") });
            return;
        }
        setOpening(true);
        setResult(null);
        let profileReady = false;
        try {
            const envelope = await fetchConfigDomain<SystemBaseData>("system-base", { force: true });
            const existingData = envelope.data || {};
            const webFetch = existingData.webFetch || {};
            const allowlist = Array.from(new Set([
                ...(Array.isArray(webFetch.agentBrowserProfileAllowlist) ? webFetch.agentBrowserProfileAllowlist : []),
                ...loginTarget.hosts,
            ].map((host) => String(host || "").trim().toLowerCase()).filter(Boolean)));
            await saveConfigDomain<SystemBaseData>("system-base", {
                data: {
                    ...existingData,
                    webFetch: {
                        ...webFetch,
                        useAgentBrowserProfile: true,
                        agentBrowserProfileAllowlist: allowlist,
                    },
                },
            });
            // Read effective config before opening the login window.
            const effectiveEnvelope = await fetchConfigDomain<SystemBaseData>("system-base", { force: true });
            const effectiveWebFetch = effectiveEnvelope.data?.webFetch || {};
            const effectiveAllowlist = new Set(
                (Array.isArray(effectiveWebFetch.agentBrowserProfileAllowlist)
                    ? effectiveWebFetch.agentBrowserProfileAllowlist
                    : [])
                    .map((host) => String(host || "").trim().toLowerCase())
                    .filter(Boolean),
            );
            const effectiveProfileEnabled = effectiveWebFetch.useAgentBrowserProfile === true;
            const effectiveHostsAllowed = loginTarget.hosts.every((host) => {
                const normalizedHost = host.toLowerCase();
                return Array.from(effectiveAllowlist).some((allowedHost) => (
                    allowedHost === normalizedHost
                    || allowedHost === `*.${normalizedHost}`
                    || (allowedHost.startsWith("*.") && normalizedHost.endsWith(allowedHost.slice(1)))
                ));
            });
            if (!effectiveProfileEnabled || !effectiveHostsAllowed) {
                setResult({
                    ok: false,
                    summary: t("app.admin.dashboard.research.runtime.agentBrowser.profileConfigFailed"),
                });
                return;
            }
            profileReady = true;

            const response = await fetch("/api/agent-browser/open", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: loginTarget.url }),
            });
            const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
            const browserOk = response.ok && payload.ok !== false;
            setResult({
                ok: browserOk,
                summary: !browserOk
                    ? responseSummary(payload, t("app.admin.dashboard.research.runtime.agentBrowser.failed"))
                    : responseSummary(payload, t("app.admin.dashboard.research.runtime.agentBrowser.opened")),
            });
        } catch (error) {
            setResult({
                ok: false,
                summary: !profileReady
                    ? t("app.admin.dashboard.research.runtime.agentBrowser.profileConfigFailed")
                    : error instanceof Error
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
            </CardContent>
            <form className="flex flex-wrap items-center gap-2 border-t px-5 py-3" onSubmit={(event) => {
                event.preventDefault();
                void openAgentBrowser();
            }}>
                <Input type="url" value={siteUrl} onChange={(event) => setSiteUrl(event.target.value)}
                    aria-label={t("app.admin.dashboard.research.runtime.agentBrowser.siteUrl")}
                    placeholder="https://example.com" required disabled={Boolean(opening)}
                    className="min-w-0 flex-1 basis-52" />
                <Button type="submit" variant="outline" disabled={Boolean(opening) || !siteUrl.trim()}>
                    {opening ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                    {t("app.admin.dashboard.research.runtime.agentBrowser.authorizeSite")}
                </Button>
            </form>
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
