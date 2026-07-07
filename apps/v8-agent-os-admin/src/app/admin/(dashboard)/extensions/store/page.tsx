"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import { Blocks, Bot, Loader2, RefreshCw, Search, Server, Sparkles, Star } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

type StoreTab = "skills" | "mcp";

type StoreListResponse<T> = {
    items: T[];
    warnings?: string[];
};

type SkillStoreItem = {
    id: string;
    name: string;
    source: string;
    skillId: string;
    installs: number;
    description?: string;
    detailUrl: string;
    installed?: boolean;
};

type SkillDetail = {
    name: string;
    source: string;
    skillId: string;
    description: string;
    markdown: string;
    detailUrl: string;
};

type McpStoreItem = {
    id: string;
    name: string;
    title: string;
    description: string;
    repositoryUrl: string;
    detailUrl: string;
    stars: number;
    avatarUrl?: string;
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
    detailUrl: string;
    repositoryUrl: string;
    candidates: McpCandidate[];
    canInstall: boolean;
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

function ownerFromName(value: string) {
    return String(value || "").split("/")[0] || "";
}

function targetLabel(target: string) {
    if (target === "env") return "ENV";
    if (target === "header") return "Header";
    if (target === "url") return "URL";
    if (target === "arg") return "Arg";
    return target || "Value";
}

function StoreIcon({ item }: { item: McpStoreItem }) {
    const title = item.title || item.name || "M";
    if (item.avatarUrl) {
        return <Image src={item.avatarUrl} alt="" width={44} height={44} className="h-11 w-11 rounded-xl border border-slate-200 bg-white object-cover p-1 dark:border-white/10 dark:bg-white" unoptimized />;
    }
    return (
        <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-slate-200 bg-white text-sm font-semibold text-slate-700 dark:border-white/10 dark:bg-white dark:text-slate-900">
            {title.charAt(0).toUpperCase()}
        </div>
    );
}

export default function ExtensionsStorePage() {
    const t = useT();
    const { toast } = useToast();
    const [activeTab, setActiveTab] = useState<StoreTab>("skills");
    const [isSwitcherVisible, setIsSwitcherVisible] = useState(true);
    const [query, setQuery] = useState("");
    const [debouncedQuery, setDebouncedQuery] = useState("");
    const [skills, setSkills] = useState<StoreListResponse<SkillStoreItem> | null>(null);
    const [mcp, setMcp] = useState<StoreListResponse<McpStoreItem> | null>(null);
    const [loading, setLoading] = useState(false);
    const [refreshing, setRefreshing] = useState(false);
    const [loadError, setLoadError] = useState("");
    const [selectedSkill, setSelectedSkill] = useState<SkillStoreItem | null>(null);
    const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null);
    const [skillDetailLoading, setSkillDetailLoading] = useState(false);
    const [skillDetailError, setSkillDetailError] = useState("");
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

    useEffect(() => {
        let lastScrollTop = 0;
        let ticking = false;

        const handleScroll = (event: Event) => {
            const target = event.target as HTMLElement;
            if (!target || target.scrollHeight === undefined) return;
            const scrollTop = target.scrollTop || 0;
            if (!ticking) {
                window.requestAnimationFrame(() => {
                    if (scrollTop < 15) {
                        setIsSwitcherVisible(true);
                    } else if (scrollTop > lastScrollTop) {
                        setIsSwitcherVisible(false);
                    } else {
                        setIsSwitcherVisible(true);
                    }
                    lastScrollTop = scrollTop;
                    ticking = false;
                });
                ticking = true;
            }
        };

        window.addEventListener("scroll", handleScroll, true);
        return () => window.removeEventListener("scroll", handleScroll, true);
    }, []);

    const loadStore = useCallback(async (refresh = false) => {
        const params = new URLSearchParams();
        if (debouncedQuery) params.set("query", debouncedQuery);
        params.set("limit", "30");
        if (refresh) params.set("refresh", "true");
        setLoadError("");
        if (refresh) setRefreshing(true);
        else setLoading(true);
        try {
            const path = activeTab === "skills" ? "skills" : "mcp";
            const res = await fetch(`/api/extensions/store/${path}?${params.toString()}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.loadFailed")));
            if (activeTab === "skills") setSkills(data as StoreListResponse<SkillStoreItem>);
            else setMcp(data as StoreListResponse<McpStoreItem>);
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

    const selectedCandidate = useMemo(
        () => (mcpDetail?.candidates || []).find((candidate) => candidate.id === selectedCandidateId) || mcpDetail?.candidates?.[0],
        [mcpDetail, selectedCandidateId],
    );

    const openSkillDetail = useCallback(async (item: SkillStoreItem, refresh = false) => {
        setSelectedSkill(item);
        setSkillDetail(null);
        setSkillDetailError("");
        setSkillDetailLoading(true);
        try {
            const params = new URLSearchParams({ source: item.source, skillId: item.skillId });
            if (refresh) params.set("refresh", "true");
            const res = await fetch(`/api/extensions/store/skills/detail?${params.toString()}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.detailFailed")));
            setSkillDetail(data as SkillDetail);
        } catch (error) {
            setSkillDetailError(error instanceof Error ? error.message : t("app.admin.dashboard.extensions.store.page.detailFailed"));
        } finally {
            setSkillDetailLoading(false);
        }
    }, [t]);

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
            if (!res.ok) throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.detailFailed")));
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
            if (!res.ok) throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.installFailed")));
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
            if (!res.ok) throw new Error(errorMessage(data, t("app.admin.dashboard.extensions.store.page.installFailed")));
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

    const itemsCount = activeTab === "skills" ? skills?.items.length || 0 : mcp?.items.length || 0;
    const warnings = activeTab === "skills" ? skills?.warnings || [] : mcp?.warnings || [];

    return (
        <div className="relative min-h-full pb-20">
            <AdminPageShell className="max-w-[1500px] gap-8">
                <AdminPageHeader
                    title="app.admin.dashboard.extensions.store.page.title"
                    description="app.admin.dashboard.extensions.store.page.description"
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

                <div className="mx-auto w-full max-w-xl">
                    <div className="relative">
                        <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                        <Input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder={activeTab === "skills" ? t("app.admin.dashboard.extensions.store.page.searchSkills") : t("app.admin.dashboard.extensions.store.page.searchMcp")}
                            className="h-12 rounded-lg border-slate-300 bg-white pl-11 text-base shadow-sm dark:border-white/10 dark:bg-slate-950"
                        />
                    </div>
                </div>

                <main className="space-y-6">
                    <div className="flex flex-wrap items-center gap-3">
                        <h2 className="text-2xl font-semibold tracking-tight text-slate-950 dark:text-slate-100">
                            {activeTab === "skills" ? t("app.admin.dashboard.extensions.store.page.allSkills") : t("app.admin.dashboard.extensions.store.page.allMcp")}
                        </h2>
                        <Badge variant="secondary" className="rounded-full">{itemsCount}</Badge>
                    </div>

                    {loadError ? <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">{loadError}</div> : null}
                    {warnings.map((warning) => <div key={warning} className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">{warning}</div>)}

                    <div className="grid gap-5 md:grid-cols-2 2xl:grid-cols-3">
                        {loading ? Array.from({ length: 9 }).map((_, index) => (
                            <div key={index} className="h-48 animate-pulse rounded-2xl border border-slate-200 bg-white/70 dark:border-white/10 dark:bg-white/10" />
                        )) : null}

                        {!loading && itemsCount === 0 ? (
                            <div className="md:col-span-2 2xl:col-span-3">
                                <EmptyState title={t("app.admin.dashboard.extensions.store.page.emptyTitle")} description={t("app.admin.dashboard.extensions.store.page.emptyDescription")} />
                            </div>
                        ) : null}

                        {!loading && activeTab === "skills" ? (skills?.items || []).map((item) => (
                            <article
                                key={item.id}
                                className="flex min-h-48 flex-col justify-between rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md dark:border-white/10 dark:bg-slate-950 dark:hover:border-white/20"
                            >
                                <div className="space-y-4">
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="min-w-0">
                                            <h3 className="truncate text-xl font-semibold text-slate-950 dark:text-slate-100">{item.name}</h3>
                                        </div>
                                        <Button size="sm" onClick={() => void installSkill(item)} disabled={installingSkillId === item.id}>
                                            {installingSkillId === item.id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                            {item.installed ? t("app.admin.dashboard.extensions.store.page.reinstall") : t("app.admin.dashboard.extensions.store.page.install")}
                                        </Button>
                                    </div>
                                    <p className="line-clamp-3 text-base leading-7 text-slate-700 dark:text-slate-300">{item.description || t("app.admin.dashboard.extensions.store.page.noDescription")}</p>
                                </div>
                                <div className="mt-6 flex items-center justify-between gap-3 text-sm text-slate-500 dark:text-slate-400">
                                    <span>{t("app.admin.dashboard.extensions.store.page.installs", { count: formatCompactNumber(item.installs) })}</span>
                                    <button type="button" onClick={() => void openSkillDetail(item)} className="font-medium text-slate-700 hover:text-slate-950 dark:text-slate-300 dark:hover:text-white">
                                        {t("app.admin.dashboard.extensions.store.page.openDetail")}
                                    </button>
                                </div>
                            </article>
                        )) : null}

                        {!loading && activeTab === "mcp" ? (mcp?.items || []).map((item) => (
                            <article
                                key={item.id}
                                className="flex min-h-48 flex-col justify-between rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md dark:border-white/10 dark:bg-slate-950 dark:hover:border-white/20"
                            >
                                <div className="space-y-4">
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="flex min-w-0 items-center gap-4">
                                            <StoreIcon item={item} />
                                            <div className="min-w-0">
                                                <h3 className="truncate text-xl font-semibold text-slate-950 dark:text-slate-100">{item.title || item.name}</h3>
                                                <div className="mt-1 text-sm font-medium text-slate-500 dark:text-slate-400">{t("app.admin.dashboard.extensions.store.page.byOwner", { owner: ownerFromName(item.name) })}</div>
                                            </div>
                                        </div>
                                        <Button size="sm" onClick={() => void openMcpDetail(item)}>
                                            {item.installed ? t("app.admin.dashboard.extensions.store.page.reinstall") : t("app.admin.dashboard.extensions.store.page.install")}
                                        </Button>
                                    </div>
                                    <p className="line-clamp-3 text-base leading-7 text-slate-700 dark:text-slate-300">{item.description || t("app.admin.dashboard.extensions.store.page.noDescription")}</p>
                                </div>
                                <div className="mt-6 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                                    <Star className="h-4 w-4" />
                                    <span>{formatCompactNumber(item.stars)}</span>
                                </div>
                            </article>
                        )) : null}
                    </div>
                </main>
            </AdminPageShell>

            <div
                className={cn(
                    "fixed bottom-6 left-1/2 z-50 -translate-x-1/2 transition-all duration-300 ease-in-out",
                    isSwitcherVisible ? "translate-y-0 scale-100 opacity-100" : "pointer-events-none translate-y-20 scale-95 opacity-0",
                )}
            >
                <div className="relative flex items-center rounded-full border border-slate-200/80 bg-white/75 p-1 shadow-[0_8px_30px_rgba(0,0,0,0.12)] backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-900/75">
                    <div
                        className="absolute bottom-1 top-1 rounded-full bg-slate-950 shadow-sm transition-all duration-300 ease-out dark:bg-slate-100"
                        style={{
                            left: activeTab === "skills" ? "4px" : "calc(50% + 2px)",
                            width: "calc(50% - 6px)",
                        }}
                    />
                    <button
                        type="button"
                        onClick={() => setActiveTab("skills")}
                        className={cn(
                            "relative z-10 flex items-center gap-1.5 rounded-full px-5 py-2 text-xs font-semibold transition-colors duration-300",
                            activeTab === "skills" ? "text-white dark:text-slate-950" : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
                        )}
                    >
                        <Sparkles className="h-3.5 w-3.5" />
                        {t("app.admin.dashboard.extensions.store.page.skills")}
                    </button>
                    <button
                        type="button"
                        onClick={() => setActiveTab("mcp")}
                        className={cn(
                            "relative z-10 flex items-center gap-1.5 rounded-full px-5 py-2 text-xs font-semibold transition-colors duration-300",
                            activeTab === "mcp" ? "text-white dark:text-slate-950" : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
                        )}
                    >
                        <Bot className="h-3.5 w-3.5" />
                        {t("app.admin.dashboard.extensions.store.page.mcp")}
                    </button>
                </div>
            </div>

            <Dialog open={Boolean(selectedSkill)} onOpenChange={(open) => {
                if (!open) {
                    setSelectedSkill(null);
                    setSkillDetail(null);
                    setSkillDetailError("");
                }
            }}>
                <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
                    {selectedSkill ? (
                        <>
                            <DialogHeader>
                                <DialogTitle>{skillDetail?.name || selectedSkill.name}</DialogTitle>
                                <DialogDescription>{skillDetail?.description || selectedSkill.description || t("app.admin.dashboard.extensions.store.page.noDescription")}</DialogDescription>
                            </DialogHeader>
                            {skillDetailLoading ? <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300"><Loader2 className="h-4 w-4 animate-spin" />{t("app.admin.dashboard.extensions.store.page.loadingDetail")}</div> : null}
                            {skillDetailError ? <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">{skillDetailError}</div> : null}
                            {skillDetail?.markdown ? (
                                <div className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-sm leading-7 text-slate-800 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-200">
                                    <ReactMarkdown>{skillDetail.markdown}</ReactMarkdown>
                                </div>
                            ) : null}
                            <DialogFooter>
                                <Button onClick={() => void installSkill(selectedSkill)} disabled={Boolean(installingSkillId)}>
                                    {installingSkillId === selectedSkill.id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
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
                                <DialogDescription>{selectedMcp.description || selectedMcp.name}</DialogDescription>
                            </DialogHeader>
                            {mcpDetailLoading ? <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300"><Loader2 className="h-4 w-4 animate-spin" />{t("app.admin.dashboard.extensions.store.page.loadingDetail")}</div> : null}
                            {mcpDetailError ? <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200">{mcpDetailError}</div> : null}
                            {mcpDetail?.warnings?.map((warning) => <div key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">{warning}</div>)}

                            {mcpDetail && mcpDetail.candidates.length > 0 ? (
                                <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/[0.03]">
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
                                            <div className="grid gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-600 dark:bg-white/[0.04] dark:text-slate-300">
                                                <div><span className="font-medium">{t("app.admin.dashboard.extensions.store.page.serverName")}</span> {selectedCandidate.serverName}</div>
                                                <div><span className="font-medium">{t("app.admin.dashboard.extensions.store.page.transport")}</span> {selectedCandidate.transport}</div>
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
                                                <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-white/10 dark:bg-white/[0.03] dark:text-slate-300">
                                                    {t("app.admin.dashboard.extensions.store.page.noRequirements")}
                                                </div>
                                            )}
                                        </>
                                    ) : null}
                                </div>
                            ) : null}
                            <DialogFooter>
                                <Button onClick={() => void installMcp()} disabled={!selectedCandidate || installingMcp}>
                                    {installingMcp ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Server className="mr-2 h-4 w-4" />}
                                    {selectedMcp.installed ? t("app.admin.dashboard.extensions.store.page.reinstall") : t("app.admin.dashboard.extensions.store.page.install")}
                                </Button>
                            </DialogFooter>
                        </>
                    ) : null}
                </DialogContent>
            </Dialog>
        </div>
    );
}
