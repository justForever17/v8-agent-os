"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Blocks, ExternalLink, Loader2, RefreshCw, Search, Server, Sparkles } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";

type StoreTab = "skills" | "mcp";

type StoreListResponse<T> = {
    provider: string;
    sourceUrl: string;
    query: string;
    freshness: "live" | "cached";
    items: T[];
    warnings?: string[];
};

type SkillStoreItem = {
    id: string;
    name: string;
    source: string;
    skillId: string;
    installs: number;
    weeklyInstalls?: number[];
    detailUrl: string;
    installCommand: string;
    installed?: boolean;
};

type McpStoreItem = {
    id: string;
    name: string;
    title: string;
    description: string;
    repositoryUrl: string;
    detailUrl: string;
    stars: number;
    language: string;
    license: string;
    topics?: string[];
    updatedAt?: string;
    serverName: string;
    installed?: boolean;
};

type McpRequirement = {
    key: string;
    target: "env" | "header" | "url" | "arg" | string;
    name: string;
    label: string;
    placeholder: string;
    required: boolean;
    secret: boolean;
    valueTemplate: string;
};

type McpCandidate = {
    id: string;
    label: string;
    serverName: string;
    transport: string;
    source: string;
    command?: string;
    url?: string;
    args?: string[];
    envKeys?: string[];
    headerKeys?: string[];
    requirements?: McpRequirement[];
};

type McpDetail = {
    id: string;
    provider: string;
    detailUrl: string;
    repositoryUrl: string;
    candidates: McpCandidate[];
    canInstall: boolean;
    freshness: "live" | "cached";
    warnings?: string[];
};

function errorMessage(payload: unknown, fallback: string) {
    if (payload && typeof payload === "object") {
        const record = payload as Record<string, unknown>;
        const detail = record.detail;
        if (detail && typeof detail === "object") {
            const detailRecord = detail as Record<string, unknown>;
            if (typeof detailRecord.message === "string") return detailRecord.message;
        }
        if (typeof record.error === "string") return record.error;
        if (typeof detail === "string") return detail;
    }
    return fallback;
}

function formatCompactNumber(value: number) {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
    return String(value || 0);
}

function targetLabel(target: string) {
    if (target === "env") return "ENV";
    if (target === "header") return "Header";
    if (target === "url") return "URL";
    if (target === "arg") return "Arg";
    return target || "Value";
}

export default function ExtensionsStorePage() {
    const t = useT();
    const { toast } = useToast();
    const [activeTab, setActiveTab] = useState<StoreTab>("skills");
    const [query, setQuery] = useState("");
    const [debouncedQuery, setDebouncedQuery] = useState("");
    const [skills, setSkills] = useState<StoreListResponse<SkillStoreItem> | null>(null);
    const [mcp, setMcp] = useState<StoreListResponse<McpStoreItem> | null>(null);
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [loadError, setLoadError] = useState("");
    const [selectedSkill, setSelectedSkill] = useState<SkillStoreItem | null>(null);
    const [installingSkillId, setInstallingSkillId] = useState("");
    const [selectedMcp, setSelectedMcp] = useState<McpStoreItem | null>(null);
    const [mcpDetail, setMcpDetail] = useState<McpDetail | null>(null);
    const [mcpDetailLoading, setMcpDetailLoading] = useState(false);
    const [mcpDetailError, setMcpDetailError] = useState("");
    const [selectedCandidateId, setSelectedCandidateId] = useState("");
    const [requirementValues, setRequirementValues] = useState<Record<string, string>>({});
    const [installingMcp, setInstallingMcp] = useState(false);

    useEffect(() => {
        const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 320);
        return () => window.clearTimeout(timer);
    }, [query]);

    const loadStore = useCallback(async (refresh = false) => {
        const params = new URLSearchParams();
        if (debouncedQuery) params.set("query", debouncedQuery);
        params.set("limit", "30");
        if (refresh) params.set("refresh", "true");
        setLoadError("");
        if (refresh) {
            setRefreshing(true);
        } else {
            setLoading(true);
        }
        try {
            const path = activeTab === "skills" ? "skills" : "mcp";
            const res = await fetch(`/api/extensions/store/${path}?${params.toString()}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.loadFailed")));
            }
            if (activeTab === "skills") {
                setSkills(data as StoreListResponse<SkillStoreItem>);
            } else {
                setMcp(data as StoreListResponse<McpStoreItem>);
            }
        } catch (error) {
            setLoadError(error instanceof Error ? error.message : t("app.admin.dashboard.extensions.store.page.loadFailed"));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [activeTab, debouncedQuery, t]);

    useEffect(() => {
        void loadStore(false);
    }, [loadStore]);

    const activeItems = activeTab === "skills" ? skills?.items || [] : mcp?.items || [];
    const activeFreshness = activeTab === "skills" ? skills?.freshness : mcp?.freshness;
    const activeWarnings = activeTab === "skills" ? skills?.warnings || [] : mcp?.warnings || [];

    const selectedCandidate = useMemo(
        () => (mcpDetail?.candidates || []).find((candidate) => candidate.id === selectedCandidateId) || mcpDetail?.candidates?.[0],
        [mcpDetail, selectedCandidateId],
    );

    const openMcpDetail = useCallback(async (item: McpStoreItem, refresh = false) => {
        setSelectedMcp(item);
        setMcpDetail(null);
        setMcpDetailError("");
        setSelectedCandidateId("");
        setRequirementValues({});
        setMcpDetailLoading(true);
        try {
            const params = new URLSearchParams({ id: item.id });
            if (refresh) params.set("refresh", "true");
            const res = await fetch(`/api/extensions/store/mcp/detail?${params.toString()}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.detailFailed")));
            }
            const detail = data as McpDetail;
            setMcpDetail(detail);
            setSelectedCandidateId(detail.candidates?.[0]?.id || "");
        } catch (error) {
            setMcpDetailError(error instanceof Error ? error.message : t("app.admin.dashboard.extensions.store.page.detailFailed"));
        } finally {
            setMcpDetailLoading(false);
        }
    }, [t]);

    const installSkill = useCallback(async (item: SkillStoreItem) => {
        setInstallingSkillId(item.id);
        try {
            const res = await fetch("/api/extensions/store/skills/install", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ source: item.source, skillId: item.skillId }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.installFailed")));
            }
            toast({
                title: t("app.admin.dashboard.extensions.store.page.skillInstalled"),
                description: t("app.admin.dashboard.extensions.store.page.skillInstalledDescription", { name: item.name }),
            });
            setSelectedSkill(null);
            await loadStore(true);
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.extensions.store.page.installFailed"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.extensions.store.page.installFailed"),
                variant: "destructive",
            });
        } finally {
            setInstallingSkillId("");
        }
    }, [loadStore, t, toast]);

    const installMcp = useCallback(async () => {
        if (!mcpDetail || !selectedCandidate) return;
        setInstallingMcp(true);
        try {
            const res = await fetch("/api/extensions/store/mcp/install", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: mcpDetail.id, candidateId: selectedCandidate.id, values: requirementValues }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.installFailed")));
            }
            toast({
                title: t("app.admin.dashboard.extensions.store.page.mcpInstalled"),
                description: t("app.admin.dashboard.extensions.store.page.mcpInstalledDescription", { name: selectedCandidate.serverName }),
            });
            setSelectedMcp(null);
            setMcpDetail(null);
            await loadStore(true);
        } catch (error) {
            toast({
                title: t("app.admin.dashboard.extensions.store.page.installFailed"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.extensions.store.page.installFailed"),
                variant: "destructive",
            });
        } finally {
            setInstallingMcp(false);
        }
    }, [loadStore, mcpDetail, requirementValues, selectedCandidate, t, toast]);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.extensions.store.page.title"
                description="app.admin.dashboard.extensions.store.page.description"
                badges={["app.admin.dashboard.extensions.store.page.badge"]}
                actions={
                    <>
                        <Button variant="outline" asChild>
                            <Link href="/admin/extensions">
                                <Blocks className="mr-2 h-4 w-4" />
                                {t("app.admin.dashboard.extensions.store.page.backToExtensions")}
                            </Link>
                        </Button>
                        <Button variant="outline" onClick={() => void loadStore(true)} disabled={refreshing || loading}>
                            {refreshing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.extensions.store.page.refresh")}
                        </Button>
                    </>
                }
            />

            <ConfigCard
                title="app.admin.dashboard.extensions.store.page.store"
                description="app.admin.dashboard.extensions.store.page.storeDescription"
                variant="list"
                bodyHeight="auto"
                bodyScroll="none"
            >
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div className="inline-flex w-full rounded-lg border border-slate-200 bg-white p-1 shadow-sm lg:w-auto dark:border-white/10 dark:bg-white/[0.03]">
                        <button
                            type="button"
                            onClick={() => setActiveTab("skills")}
                            className={`flex min-h-9 flex-1 items-center justify-center rounded-md px-4 text-sm font-medium transition lg:min-w-32 ${activeTab === "skills" ? "bg-slate-900 text-white shadow-sm dark:bg-slate-100 dark:text-slate-900" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-white/10"}`}
                        >
                            <Sparkles className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.extensions.store.page.skills")}
                        </button>
                        <button
                            type="button"
                            onClick={() => setActiveTab("mcp")}
                            className={`flex min-h-9 flex-1 items-center justify-center rounded-md px-4 text-sm font-medium transition lg:min-w-32 ${activeTab === "mcp" ? "bg-slate-900 text-white shadow-sm dark:bg-slate-100 dark:text-slate-900" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-white/10"}`}
                        >
                            <Server className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.extensions.store.page.mcp")}
                        </button>
                    </div>
                    <div className="relative w-full lg:max-w-md">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                        <Input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder={activeTab === "skills" ? t("app.admin.dashboard.extensions.store.page.searchSkills") : t("app.admin.dashboard.extensions.store.page.searchMcp")}
                            className="h-10 pl-9"
                        />
                    </div>
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                    <Badge variant="outline">{activeTab === "skills" ? "skills.sh" : "github.com/mcp"}</Badge>
                    {activeFreshness ? <Badge variant={activeFreshness === "live" ? "default" : "secondary"}>{activeFreshness === "live" ? t("app.admin.dashboard.extensions.store.page.live") : t("app.admin.dashboard.extensions.store.page.cached")}</Badge> : null}
                    {activeWarnings.map((warning) => <span key={warning}>{warning}</span>)}
                </div>

                {loadError ? <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">{loadError}</div> : null}

                <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {loading ? Array.from({ length: 6 }).map((_, index) => (
                        <div key={index} className="h-44 animate-pulse rounded-lg border border-slate-200 bg-slate-100 dark:border-white/10 dark:bg-white/10" />
                    )) : null}
                    {!loading && activeItems.length === 0 ? (
                        <div className="md:col-span-2 xl:col-span-3">
                            <EmptyState title={t("app.admin.dashboard.extensions.store.page.emptyTitle")} description={t("app.admin.dashboard.extensions.store.page.emptyDescription")} />
                        </div>
                    ) : null}
                    {!loading && activeTab === "skills" ? (skills?.items || []).map((item) => (
                        <button
                            key={item.id}
                            type="button"
                            onClick={() => setSelectedSkill(item)}
                            className="flex h-44 flex-col justify-between rounded-lg border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-slate-300 hover:shadow-md dark:border-white/10 dark:bg-white/[0.03] dark:hover:border-white/20"
                        >
                            <div className="min-w-0 space-y-2">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{item.name}</div>
                                        <div className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">{item.source}</div>
                                    </div>
                                    {item.installed ? <Badge variant="secondary">{t("app.admin.dashboard.extensions.store.page.installed")}</Badge> : null}
                                </div>
                                <div className="font-mono text-xs text-slate-500 dark:text-slate-400">{item.installCommand}</div>
                            </div>
                            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                                <span>{t("app.admin.dashboard.extensions.store.page.installs", { count: formatCompactNumber(item.installs) })}</span>
                                <span>{t("app.admin.dashboard.extensions.store.page.openDetail")}</span>
                            </div>
                        </button>
                    )) : null}
                    {!loading && activeTab === "mcp" ? (mcp?.items || []).map((item) => (
                        <button
                            key={item.id}
                            type="button"
                            onClick={() => void openMcpDetail(item)}
                            className="flex h-52 flex-col justify-between rounded-lg border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-slate-300 hover:shadow-md dark:border-white/10 dark:bg-white/[0.03] dark:hover:border-white/20"
                        >
                            <div className="min-w-0 space-y-2">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{item.title || item.name}</div>
                                        <div className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">{item.name}</div>
                                    </div>
                                    {item.installed ? <Badge variant="secondary">{t("app.admin.dashboard.extensions.store.page.installed")}</Badge> : null}
                                </div>
                                <p className="line-clamp-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{item.description || t("app.admin.dashboard.extensions.store.page.noDescription")}</p>
                            </div>
                            <div className="space-y-2">
                                <div className="flex flex-wrap gap-1">
                                    {item.language ? <Badge variant="outline">{item.language}</Badge> : null}
                                    {(item.topics || []).slice(0, 3).map((topic) => <Badge key={topic} variant="outline">{topic}</Badge>)}
                                </div>
                                <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                                    <span>{t("app.admin.dashboard.extensions.store.page.stars", { count: formatCompactNumber(item.stars) })}</span>
                                    <span>{t("app.admin.dashboard.extensions.store.page.openDetail")}</span>
                                </div>
                            </div>
                        </button>
                    )) : null}
                </div>
            </ConfigCard>

            <Dialog open={Boolean(selectedSkill)} onOpenChange={(open) => !open && setSelectedSkill(null)}>
                <DialogContent className="max-w-2xl">
                    {selectedSkill ? (
                        <>
                            <DialogHeader>
                                <DialogTitle>{selectedSkill.name}</DialogTitle>
                                <DialogDescription>{selectedSkill.source}</DialogDescription>
                            </DialogHeader>
                            <div className="space-y-4">
                                <div className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm dark:border-white/10 dark:bg-white/[0.03]">
                                    <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">skills.sh</Badge><span>{t("app.admin.dashboard.extensions.store.page.installs", { count: formatCompactNumber(selectedSkill.installs) })}</span></div>
                                    <div className="break-all font-mono text-xs text-slate-600 dark:text-slate-300">{selectedSkill.installCommand}</div>
                                </div>
                                <Button variant="outline" asChild>
                                    <a href={selectedSkill.detailUrl} target="_blank" rel="noreferrer">
                                        <ExternalLink className="mr-2 h-4 w-4" />
                                        {t("app.admin.dashboard.extensions.store.page.viewSource")}
                                    </a>
                                </Button>
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={() => setSelectedSkill(null)}>{t("app.admin.dashboard.extensions.store.page.cancel")}</Button>
                                <Button onClick={() => void installSkill(selectedSkill)} disabled={Boolean(installingSkillId)}>
                                    {installingSkillId === selectedSkill.id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                                    {selectedSkill.installed ? t("app.admin.dashboard.extensions.store.page.reinstall") : t("app.admin.dashboard.extensions.store.page.install")}
                                </Button>
                            </DialogFooter>
                        </>
                    ) : null}
                </DialogContent>
            </Dialog>

            <Dialog open={Boolean(selectedMcp)} onOpenChange={(open) => {
                if (!open) {
                    setSelectedMcp(null);
                    setMcpDetail(null);
                    setMcpDetailError("");
                    setRequirementValues({});
                }
            }}>
                <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
                    {selectedMcp ? (
                        <>
                            <DialogHeader>
                                <DialogTitle>{selectedMcp.title || selectedMcp.name}</DialogTitle>
                                <DialogDescription>{selectedMcp.name}</DialogDescription>
                            </DialogHeader>
                            <div className="space-y-4">
                                <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">{selectedMcp.description || t("app.admin.dashboard.extensions.store.page.noDescription")}</p>
                                <div className="flex flex-wrap items-center gap-2">
                                    <Badge variant="outline">github.com/mcp</Badge>
                                    {selectedMcp.language ? <Badge variant="outline">{selectedMcp.language}</Badge> : null}
                                    <Badge variant="outline">{t("app.admin.dashboard.extensions.store.page.stars", { count: formatCompactNumber(selectedMcp.stars) })}</Badge>
                                    <Button variant="outline" size="sm" asChild>
                                        <a href={selectedMcp.detailUrl} target="_blank" rel="noreferrer">
                                            <ExternalLink className="mr-2 h-4 w-4" />
                                            {t("app.admin.dashboard.extensions.store.page.viewSource")}
                                        </a>
                                    </Button>
                                </div>

                                {mcpDetailLoading ? <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300"><Loader2 className="h-4 w-4 animate-spin" />{t("app.admin.dashboard.extensions.store.page.loadingDetail")}</div> : null}
                                {mcpDetailError ? <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">{mcpDetailError}</div> : null}
                                {mcpDetail?.warnings?.map((warning) => <div key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">{warning}</div>)}

                                {mcpDetail && mcpDetail.candidates.length > 0 ? (
                                    <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/[0.03]">
                                        <div className="space-y-2">
                                            <Label>{t("app.admin.dashboard.extensions.store.page.installMethod")}</Label>
                                            <Select value={selectedCandidate?.id || ""} onValueChange={(value) => {
                                                setSelectedCandidateId(value);
                                                setRequirementValues({});
                                            }}>
                                                <SelectTrigger>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {mcpDetail.candidates.map((candidate) => (
                                                        <SelectItem key={candidate.id} value={candidate.id}>
                                                            {candidate.label} · {candidate.serverName}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>

                                        {selectedCandidate ? (
                                            <>
                                                <div className="grid gap-2 rounded-md bg-slate-50 p-3 text-xs text-slate-600 dark:bg-white/[0.04] dark:text-slate-300">
                                                    <div><span className="font-medium">{t("app.admin.dashboard.extensions.store.page.serverName")}</span> {selectedCandidate.serverName}</div>
                                                    <div><span className="font-medium">{t("app.admin.dashboard.extensions.store.page.transport")}</span> {selectedCandidate.transport}</div>
                                                    {selectedCandidate.url ? <div className="break-all"><span className="font-medium">URL</span> {selectedCandidate.url}</div> : null}
                                                    {selectedCandidate.command ? <div className="break-all"><span className="font-medium">Command</span> {selectedCandidate.command} {(selectedCandidate.args || []).join(" ")}</div> : null}
                                                </div>

                                                {(selectedCandidate.requirements || []).length > 0 ? (
                                                    <div className="grid gap-3">
                                                        {(selectedCandidate.requirements || []).map((requirement) => (
                                                            <div key={requirement.key} className="space-y-2">
                                                                <Label className="flex flex-wrap items-center gap-2">
                                                                    <span>{requirement.label || requirement.name}</span>
                                                                    <Badge variant="outline">{targetLabel(requirement.target)}</Badge>
                                                                    {requirement.required ? <Badge variant="secondary">{t("app.admin.dashboard.extensions.store.page.required")}</Badge> : null}
                                                                </Label>
                                                                <Input
                                                                    type={requirement.secret ? "password" : "text"}
                                                                    autoComplete="off"
                                                                    value={requirementValues[requirement.key] || ""}
                                                                    onChange={(event) => setRequirementValues((current) => ({ ...current, [requirement.key]: event.target.value }))}
                                                                    placeholder={requirement.name || requirement.placeholder}
                                                                />
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : (
                                                    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300">
                                                        {t("app.admin.dashboard.extensions.store.page.noRequirements")}
                                                    </div>
                                                )}
                                            </>
                                        ) : null}
                                    </div>
                                ) : null}
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={() => setSelectedMcp(null)}>{t("app.admin.dashboard.extensions.store.page.cancel")}</Button>
                                <Button onClick={() => void installMcp()} disabled={!selectedCandidate || installingMcp}>
                                    {installingMcp ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Server className="mr-2 h-4 w-4" />}
                                    {selectedMcp.installed ? t("app.admin.dashboard.extensions.store.page.reinstall") : t("app.admin.dashboard.extensions.store.page.install")}
                                </Button>
                            </DialogFooter>
                        </>
                    ) : null}
                </DialogContent>
            </Dialog>
        </AdminPageShell>
    );
}
