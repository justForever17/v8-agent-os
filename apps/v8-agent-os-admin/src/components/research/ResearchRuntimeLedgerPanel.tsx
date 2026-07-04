"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, GitBranch, RefreshCw, RotateCcw, Search, Trash2 } from "lucide-react";

import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

type EvidenceBundle = {
    evidenceBundleId?: string;
    question?: string;
    confidence?: string;
    authorityScore?: number;
    createdAt?: string;
    sourceMatrix?: Array<{ title?: string; host?: string; url?: string; authorityScore?: number }>;
};

type ResearchAnswerPack = {
    answer?: string;
    sources?: Array<{ title?: string; host?: string; url?: string; authorityScore?: number; relevance?: number | string; freshness?: string }>;
    score?: { label?: string; confidence?: string; authorityScore?: number; qualityStatus?: string; reuseDecision?: string };
    limitations?: string[];
    missingOrStaleReasons?: string[];
};

type ExperiencePack = {
    experiencePackId?: string;
    title?: string;
    query?: string;
    summary?: string;
    resultPreview?: string;
    researchResult?: string;
    answer?: string;
    findings?: string;
    applicability?: string;
    status?: string;
    confidence?: string;
    authorityScore?: number;
    usageCount?: number;
    lastUsedAt?: string | null;
    archivedAt?: string | null;
    qualityStatus?: string;
    invalidationReason?: string;
    researchAnswerPack?: ResearchAnswerPack;
    missingEvidence?: string[];
    limitations?: string[];
    sourceUrls?: string[];
    claimDigest?: Array<{ claim?: string }>;
    sourceMatrixDigest?: Array<{ title?: string; host?: string; url?: string; authorityScore?: number }>;
    createdFromBundleId?: string;
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

function packStateTone(state: "searchable" | "review" | "refresh" | "archived" | "unknown") {
    if (state === "searchable") return "bg-emerald-50 text-emerald-700 border-emerald-200";
    if (state === "review") return "bg-amber-50 text-amber-700 border-amber-200";
    if (state === "refresh") return "bg-rose-50 text-rose-700 border-rose-200";
    if (state === "archived") return "bg-slate-100 text-slate-600 border-slate-200";
    return "bg-slate-50 text-slate-600 border-slate-200";
}

function normalizeStatus(value?: string) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "active" || normalized === "draft" || normalized === "archived") return normalized;
    return "unknown";
}

function normalizeErrorCode(value?: string) {
    const normalized = String(value || "").trim();
    const lowered = normalized.toLowerCase();
    if (!normalized) return "unknown";
    if (lowered.includes("experience pack not found")) return "experience_pack_not_found";
    if (lowered.includes("evidence bundle not found")) return "evidence_bundle_not_found";
    if (
        [
            "research_runtime_load_failed",
            "research_runtime_search_failed",
            "research_runtime_promote_failed",
            "research_runtime_archive_failed",
            "research_runtime_restore_failed",
            "research_runtime_delete_failed",
        ].includes(lowered)
    ) {
        return lowered;
    }
    return "unknown";
}

function resolvePackState(item: ExperiencePack, t: ReturnType<typeof useT>) {
    const status = normalizeStatus(item.status);
    const qualityStatus = String(item.qualityStatus || "").trim();
    const invalidationReason = String(item.invalidationReason || "").trim();
    const answerPack = item.researchAnswerPack || {};
    const hasAnswer = Boolean(String(answerPack.answer || item.researchResult || item.answer || "").trim());
    const hasClaims = Array.isArray(item.claimDigest) && item.claimDigest.length > 0;
    const isRefreshNeeded = qualityStatus === "low_quality_pack" || qualityStatus === "refresh_required" || Boolean(invalidationReason);
    if (status === "archived") {
        return { key: "archived" as const, label: t("app.admin.dashboard.research.runtime.ledger.packState.archived") };
    }
    if (isRefreshNeeded) {
        return { key: "refresh" as const, label: t("app.admin.dashboard.research.runtime.ledger.packState.refresh") };
    }
    if (status === "draft" || (!hasAnswer && !hasClaims)) {
        return { key: "review" as const, label: t("app.admin.dashboard.research.runtime.ledger.packState.review") };
    }
    if (status === "active") {
        return { key: "searchable" as const, label: t("app.admin.dashboard.research.runtime.ledger.packState.searchable") };
    }
    return { key: "unknown" as const, label: t("app.admin.dashboard.research.runtime.ledger.packState.unknown") };
}

function buildExperiencePackHoverLines(item: ExperiencePack, t: ReturnType<typeof useT>) {
    const lines: string[] = [];
    const qualityStatus = String(item.qualityStatus || "").trim();
    const invalidationReason = String(item.invalidationReason || "").trim();
    const isArchived = normalizeStatus(item.status) === "archived";
    const answerPack = item.researchAnswerPack || {};
    const answer = String(answerPack.answer || item.researchResult || "").trim();
    const score = answerPack.score || {};
    const isLowQuality = qualityStatus === "low_quality_pack" || Boolean(invalidationReason);
    const applicability = String(item.applicability || "").trim();
    const stateLabel = isArchived
        ? t("app.admin.dashboard.research.runtime.ledger.hover.stateArchived")
        : isLowQuality
          ? t("app.admin.dashboard.research.runtime.ledger.hover.stateRefresh")
          : answer || (Array.isArray(item.claimDigest) && item.claimDigest.length)
            ? t("app.admin.dashboard.research.runtime.ledger.hover.stateReusable")
            : t("app.admin.dashboard.research.runtime.ledger.hover.stateLowQuality");
    lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.state")}: ${stateLabel}`);
    if (answer) lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.answer")}: ${answer.length > 1200 ? `${answer.slice(0, 1200)}…` : answer}`);
    for (const itemClaim of (!answer && Array.isArray(item.claimDigest) ? item.claimDigest : []).slice(0, 3)) {
        const claim = String(itemClaim?.claim || "").trim();
        if (claim) lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.answer")}: ${claim.length > 420 ? `${claim.slice(0, 420)}…` : claim}`);
    }
    const missing = Array.isArray(item.missingEvidence) ? item.missingEvidence : [];
    const limitations = Array.isArray(item.limitations) ? item.limitations : [];
    if (score.label || score.confidence || score.authorityScore !== undefined || score.qualityStatus) {
        lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.score")}: ${score.label || `${score.confidence || "unknown"} / authority=${score.authorityScore ?? "n/a"} / ${score.qualityStatus || stateLabel}`}`);
    } else if (item.confidence || item.authorityScore !== undefined) {
        lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.score")}: ${item.confidence || "unknown"} / authority=${item.authorityScore ?? "n/a"}`);
    }
    if (!answer && !(Array.isArray(item.claimDigest) && item.claimDigest.length)) {
        lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.missingEvidence")}: ${missing[0] || invalidationReason || t("app.admin.dashboard.research.runtime.ledger.hover.refreshRequired")}`);
    }
    for (const reason of missing.slice(0, 2)) {
        const text = String(reason || "").trim();
        if (text) lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.missingEvidence")}: ${text.length > 320 ? `${text.slice(0, 320)}…` : text}`);
    }
    for (const reason of limitations.slice(0, 2)) {
        const text = String(reason || "").trim();
        if (text) lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.limitations")}: ${text.length > 320 ? `${text.slice(0, 320)}…` : text}`);
    }
    if (applicability) lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.applicability")}: ${applicability}`);
    const sources = Array.isArray(answerPack.sources) && answerPack.sources.length ? answerPack.sources : (Array.isArray(item.sourceMatrixDigest) ? item.sourceMatrixDigest : []);
    for (const source of sources.slice(0, 4)) {
        const title = String(source.title || source.host || source.url || "").trim();
        if (!title) continue;
        const host = String(source.host || "").trim();
        lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.source")}: ${title}${host && host !== title ? ` · ${host}` : ""}`);
    }
    for (const url of (Array.isArray(item.sourceUrls) ? item.sourceUrls : []).slice(0, Math.max(0, 4 - sources.length))) {
        const text = String(url || "").trim();
        if (text) lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.source")}: ${text}`);
    }
    if (!lines.length && item.experiencePackId) {
        lines.push(`${t("app.admin.dashboard.research.runtime.ledger.hover.experiencePack")}: ${item.experiencePackId}`);
    }
    return lines;
}

export function ResearchRuntimeLedgerPanel() {
    const t = useT();
    const [data, setData] = useState<LedgerPayload | null>(null);
    const [query, setQuery] = useState("");
    const [packs, setPacks] = useState<ExperiencePack[]>([]);
    const [includeArchived, setIncludeArchived] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const evidence = useMemo(() => data?.evidenceBundles || [], [data]);
    const timeline = useMemo(() => data?.confidenceTimeline || [], [data]);
    const visiblePacks = packs.length ? packs : data?.experiencePacks || [];

    const refresh = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const params = new URLSearchParams({ view: "ledger", scope: "global", limit: "30" });
            if (includeArchived) params.set("includeArchived", "true");
            const response = await fetch(`/api/research-runtime?${params.toString()}`, { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload?.detail || payload?.error || "research_runtime_load_failed");
            setData(payload);
        } catch (err) {
            setError(err instanceof Error ? err.message : "research_runtime_load_failed");
        } finally {
            setLoading(false);
        }
    }, [includeArchived]);

    const searchPacks = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const params = new URLSearchParams({ view: "experience", scope: "global", limit: "30" });
            if (query.trim()) params.set("query", query.trim());
            if (includeArchived) params.set("includeArchived", "true");
            const response = await fetch(`/api/research-runtime?${params.toString()}`, { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload?.detail || payload?.error || "research_runtime_search_failed");
            setPacks(Array.isArray(payload.items) ? payload.items : []);
        } catch (err) {
            setError(err instanceof Error ? err.message : "research_runtime_search_failed");
        } finally {
            setLoading(false);
        }
    }, [includeArchived, query]);

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

    const mutatePack = useCallback(
        async (action: "archive" | "restore", packId?: string) => {
            if (!packId) return;
            setLoading(true);
            setError("");
            try {
                const response = await fetch("/api/research-runtime", {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ action, experiencePackId: packId, initiatedBy: "admin_research_runtime" }),
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload?.detail || payload?.error || `research_runtime_${action}_failed`);
                await refresh();
                await searchPacks();
            } catch (err) {
                setError(err instanceof Error ? err.message : `research_runtime_${action}_failed`);
            } finally {
                setLoading(false);
            }
        },
        [refresh, searchPacks],
    );

    const hardDeletePack = useCallback(
        async (pack: ExperiencePack) => {
            const packId = String(pack.experiencePackId || "").trim();
            if (!packId) return;
            const label = pack.title || packId;
            if (!window.confirm(t("app.admin.dashboard.research.runtime.ledger.deleteConfirm", { label }))) return;
            setLoading(true);
            setError("");
            try {
                const params = new URLSearchParams({ experiencePackId: packId, confirm: "true" });
                const response = await fetch(`/api/research-runtime?${params.toString()}`, { method: "DELETE" });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload?.detail || payload?.error || "research_runtime_delete_failed");
                await refresh();
                await searchPacks();
            } catch (err) {
                setError(err instanceof Error ? err.message : "research_runtime_delete_failed");
            } finally {
                setLoading(false);
            }
        },
        [refresh, searchPacks, t],
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
                {error ? (
                    <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                        {t(`app.admin.dashboard.research.runtime.ledger.errors.${normalizeErrorCode(error)}`)}
                        {normalizeErrorCode(error) === "unknown" ? <div className="mt-1 font-mono text-xs text-rose-600">{error}</div> : null}
                    </div>
                ) : null}
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
                            <label className="flex items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs text-slate-600">
                                <Checkbox checked={includeArchived} onCheckedChange={(value) => {
                                    setIncludeArchived(Boolean(value));
                                    setPacks([]);
                                }} />
                                {t("app.admin.dashboard.research.runtime.ledger.includeArchived")}
                            </label>
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
                        {visiblePacks.slice(0, 8).map((item) => {
                            const packState = resolvePackState(item, t);
                            return (
                                <div key={item.experiencePackId} className="rounded-2xl border border-slate-200 bg-white p-4">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <AdminHoverInfo
                                                content={(
                                                    <div className="space-y-1">
                                                        {buildExperiencePackHoverLines(item, t).map((line, index) => (
                                                            <div key={index} className="whitespace-normal break-words">{line}</div>
                                                        ))}
                                                    </div>
                                                )}
                                                panelClassName="w-[32rem] max-w-[calc(100vw-2rem)] whitespace-normal text-xs leading-5"
                                            >
                                                <div className="font-medium text-slate-900 underline decoration-slate-300 decoration-dotted underline-offset-4">
                                                    {item.title || item.experiencePackId}
                                                </div>
                                            </AdminHoverInfo>
                                            <div className="mt-1 text-xs text-slate-500">{item.experiencePackId}</div>
                                        </div>
                                        <Badge variant="outline" className={packStateTone(packState.key)}>
                                            {packState.label}
                                        </Badge>
                                    </div>
                                    <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
                                        <span>{t("app.admin.dashboard.research.runtime.ledger.authority")}: {item.authorityScore ?? 0}</span>
                                        <span>{t("app.admin.dashboard.research.runtime.ledger.usage")}: {item.usageCount ?? 0}</span>
                                        <span>{t("app.admin.dashboard.research.runtime.ledger.confidence")}: {item.confidence || t("app.admin.dashboard.research.runtime.ledger.status.unknown")}</span>
                                    </div>
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        {normalizeStatus(item.status) === "archived" ? (
                                            <Button type="button" variant="outline" size="sm" onClick={() => mutatePack("restore", item.experiencePackId)} disabled={loading}>
                                                <RotateCcw className="mr-2 h-4 w-4" />
                                                {t("app.admin.dashboard.research.runtime.ledger.restore")}
                                            </Button>
                                        ) : (
                                            <Button type="button" variant="outline" size="sm" onClick={() => mutatePack("archive", item.experiencePackId)} disabled={loading}>
                                                <Archive className="mr-2 h-4 w-4" />
                                                {t("app.admin.dashboard.research.runtime.ledger.archive")}
                                            </Button>
                                        )}
                                        <Button type="button" variant="outline" size="sm" className="border-rose-200 text-rose-700 hover:bg-rose-50" onClick={() => hardDeletePack(item)} disabled={loading}>
                                            <Trash2 className="mr-2 h-4 w-4" />
                                            {t("app.admin.dashboard.research.runtime.ledger.hardDelete")}
                                        </Button>
                                    </div>
                                </div>
                            );
                        })}
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
                                            {item.confidence || t("app.admin.dashboard.research.runtime.ledger.status.unknown")}
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
                                        <div className="text-xs text-slate-500">{item.at} · {item.confidence || t("app.admin.dashboard.research.runtime.ledger.status.unknown")} · {item.authorityScore ?? 0}</div>
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
