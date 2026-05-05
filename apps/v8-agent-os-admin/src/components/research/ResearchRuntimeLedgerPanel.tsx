"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, GitBranch, RefreshCw, Search } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type EvidenceBundle = {
    evidenceBundleId?: string;
    question?: string;
    confidence?: string;
    authorityScore?: number;
    createdAt?: string;
    sourceMatrix?: Array<{ title?: string; host?: string; url?: string; authorityScore?: number }>;
};

type ExperiencePack = {
    experiencePackId?: string;
    title?: string;
    status?: string;
    confidence?: string;
    authorityScore?: number;
    usageCount?: number;
    lastUsedAt?: string | null;
    sourceMatrixDigest?: Array<{ title?: string; host?: string; url?: string; authorityScore?: number }>;
};

type LedgerPayload = {
    ok?: boolean;
    counts?: { evidenceBundles?: number; experiencePacks?: number };
    evidenceBundles?: EvidenceBundle[];
    experiencePacks?: ExperiencePack[];
    confidenceTimeline?: Array<{ at?: string; question?: string; confidence?: string; authorityScore?: number; evidenceBundleId?: string }>;
};

function confidenceTone(confidence?: string) {
    if (confidence === "high") return "bg-emerald-50 text-emerald-700 border-emerald-200";
    if (confidence === "medium") return "bg-sky-50 text-sky-700 border-sky-200";
    return "bg-slate-50 text-slate-600 border-slate-200";
}

export function ResearchRuntimeLedgerPanel() {
    const t = useT();
    const [data, setData] = useState<LedgerPayload | null>(null);
    const [query, setQuery] = useState("");
    const [packs, setPacks] = useState<ExperiencePack[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const evidence = useMemo(() => data?.evidenceBundles || [], [data]);
    const timeline = useMemo(() => data?.confidenceTimeline || [], [data]);
    const visiblePacks = packs.length ? packs : data?.experiencePacks || [];

    const refresh = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const response = await fetch("/api/research-runtime?view=ledger&scope=global&limit=30", { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload?.detail || payload?.error || "research_runtime_load_failed");
            setData(payload);
        } catch (err) {
            setError(err instanceof Error ? err.message : "research_runtime_load_failed");
        } finally {
            setLoading(false);
        }
    }, []);

    const searchPacks = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const params = new URLSearchParams({ view: "experience", scope: "global", limit: "30" });
            if (query.trim()) params.set("query", query.trim());
            const response = await fetch(`/api/research-runtime?${params.toString()}`, { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload?.detail || payload?.error || "research_runtime_search_failed");
            setPacks(Array.isArray(payload.items) ? payload.items : []);
        } catch (err) {
            setError(err instanceof Error ? err.message : "research_runtime_search_failed");
        } finally {
            setLoading(false);
        }
    }, [query]);

    const promote = useCallback(
        async (bundleId?: string) => {
            if (!bundleId) return;
            setLoading(true);
            setError("");
            try {
                const response = await fetch("/api/research-runtime", {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ evidenceBundleId: bundleId }),
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload?.detail || payload?.error || "research_runtime_promote_failed");
                await refresh();
                await searchPacks();
            } catch (err) {
                setError(err instanceof Error ? err.message : "research_runtime_promote_failed");
            } finally {
                setLoading(false);
            }
        },
        [refresh, searchPacks],
    );

    useEffect(() => {
        void refresh();
    }, [refresh]);

    return (
        <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="text-lg font-semibold text-slate-900">
                        {t("app.admin.dashboard.research.runtime.ledger.title")}
                    </CardTitle>
                    <p className="mt-2 text-sm text-slate-500">
                        {t("app.admin.dashboard.research.runtime.ledger.description")}
                    </p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={refresh} disabled={loading}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    {t("app.admin.dashboard.research.runtime.ledger.refresh")}
                </Button>
            </CardHeader>
            <CardContent className="space-y-5">
                {error ? <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}
                <div className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t("app.admin.dashboard.research.runtime.ledger.evidenceCount")}</div>
                        <div className="mt-2 text-2xl font-semibold text-slate-950">{data?.counts?.evidenceBundles ?? evidence.length}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t("app.admin.dashboard.research.runtime.ledger.packCount")}</div>
                        <div className="mt-2 text-2xl font-semibold text-slate-950">{data?.counts?.experiencePacks ?? visiblePacks.length}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                        <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{t("app.admin.dashboard.research.runtime.ledger.timelineCount")}</div>
                        <div className="mt-2 text-2xl font-semibold text-slate-950">{timeline.length}</div>
                    </div>
                </div>

                <section className="space-y-3">
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.research.runtime.ledger.experiencePacks")}</h3>
                        <div className="flex gap-2">
                            <Input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder={t("app.admin.dashboard.research.runtime.ledger.searchPlaceholder")}
                                className="h-9 w-full md:w-72"
                            />
                            <Button type="button" size="sm" onClick={searchPacks} disabled={loading}>
                                <Search className="mr-2 h-4 w-4" />
                                {t("app.admin.dashboard.research.runtime.ledger.search")}
                            </Button>
                        </div>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-2">
                        {visiblePacks.slice(0, 8).map((item) => (
                            <div key={item.experiencePackId} className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="font-medium text-slate-900">{item.title || item.experiencePackId}</div>
                                        <div className="mt-1 text-xs text-slate-500">{item.experiencePackId}</div>
                                    </div>
                                    <Badge variant="outline" className={confidenceTone(item.confidence)}>
                                        {item.confidence || "unknown"}
                                    </Badge>
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
                                    <span>{t("app.admin.dashboard.research.runtime.ledger.authority")}: {item.authorityScore ?? 0}</span>
                                    <span>{t("app.admin.dashboard.research.runtime.ledger.usage")}: {item.usageCount ?? 0}</span>
                                    <span>{item.status || "draft"}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                </section>

                <section className="space-y-3">
                    <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.research.runtime.ledger.evidenceBundles")}</h3>
                    <div className="space-y-3">
                        {evidence.slice(0, 8).map((item) => (
                            <div key={item.evidenceBundleId} className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                                    <div>
                                        <div className="font-medium text-slate-900">{item.question || item.evidenceBundleId}</div>
                                        <div className="mt-1 text-xs text-slate-500">{item.evidenceBundleId}</div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Badge variant="outline" className={confidenceTone(item.confidence)}>
                                            {item.confidence || "unknown"}
                                        </Badge>
                                        <Button type="button" variant="outline" size="sm" onClick={() => promote(item.evidenceBundleId)} disabled={loading}>
                                            <Archive className="mr-2 h-4 w-4" />
                                            {t("app.admin.dashboard.research.runtime.ledger.promote")}
                                        </Button>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </section>

                <section className="space-y-3">
                    <h3 className="text-sm font-semibold text-slate-900">{t("app.admin.dashboard.research.runtime.ledger.confidenceTimeline")}</h3>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                        <div className="space-y-3">
                            {timeline.slice(0, 10).map((item) => (
                                <div key={`${item.evidenceBundleId}-${item.at}`} className="flex items-start gap-3 text-sm">
                                    <GitBranch className="mt-0.5 h-4 w-4 text-sky-600" />
                                    <div className="min-w-0">
                                        <div className="truncate font-medium text-slate-900">{item.question || item.evidenceBundleId}</div>
                                        <div className="text-xs text-slate-500">{item.at} · {item.confidence || "unknown"} · {item.authorityScore ?? 0}</div>
                                    </div>
                                </div>
                            ))}
                            {!timeline.length ? <div className="text-sm text-slate-500">{t("app.admin.dashboard.research.runtime.ledger.emptyTimeline")}</div> : null}
                        </div>
                    </div>
                </section>
            </CardContent>
        </Card>
    );
}
