"use client";

import { useCallback, useMemo, useState } from "react";
import { ChevronDown, ExternalLink, Loader2, RefreshCw, Save, Search, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";
import { fetchConfigDomain, peekConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";

type SourceProvider = {
    id: string;
    displayName: string;
    region: string;
    role: string;
    supports: string[];
    costTier: string;
    latencyTier: string;
    requiresProxy: boolean | "auto" | "config";
    supportsLoginProfile?: boolean;
    outputFormats: string[];
    implemented: boolean;
    enabled: boolean;
    authEnv: string;
    hasConfiguredKey: boolean;
    baseUrl: string;
    credentialHelp?: {
        label?: string;
        url?: string;
    };
};

type SourceProvidersPayload = {
    ok: boolean;
    sourceRouter: Record<string, unknown>;
    providers: SourceProvider[];
};

type SystemBaseData = {
    webFetch?: {
        sourceRouter?: Record<string, unknown>;
        providers?: Record<string, Record<string, unknown>>;
        [key: string]: unknown;
    };
    [key: string]: unknown;
};

type EditableProvider = SourceProvider & {
    apiKeyDraft: string;
};

const SOURCE_PROVIDERS_URL = "/api/research-runtime?view=source-providers";

const REGION_LABELS: Record<string, string> = {
    cn: "app.admin.research.sourceProviders.region.cn",
    global: "app.admin.research.sourceProviders.region.global",
    self_host: "app.admin.research.sourceProviders.region.selfHost",
};

const ROLE_LABELS: Record<string, string> = {
    discovery: "app.admin.research.sourceProviders.role.discovery",
    read_extract: "app.admin.research.sourceProviders.role.readExtract",
    deep_answer: "app.admin.research.sourceProviders.role.deepAnswer",
};

function mergeProviderConfig(provider: SourceProvider, envelope: ConfigRegistryEnvelope<SystemBaseData> | null): EditableProvider {
    const configured = envelope?.data?.webFetch?.providers?.[provider.id] || {};
    return {
        ...provider,
        enabled: Boolean(configured.enabled ?? provider.enabled),
        authEnv: String(configured.authEnv ?? provider.authEnv ?? ""),
        baseUrl: String(configured.baseUrl ?? provider.baseUrl ?? ""),
        hasConfiguredKey: Boolean(configured.apiKey || configured.credential || configured.key || provider.hasConfiguredKey),
        apiKeyDraft: "",
    };
}

export function ResearchSourceProviderPanel() {
    const { toast } = useToast();
    const t = useT();
    const cachedProviders = peekAdminJsonCache<SourceProvidersPayload>(SOURCE_PROVIDERS_URL);
    const cachedSystemBase = peekConfigDomain<SystemBaseData>("system-base");
    const [providers, setProviders] = useState<EditableProvider[]>(
        () => (cachedProviders?.providers || []).map((item) => mergeProviderConfig(item, cachedSystemBase || null)),
    );
    const [sourceRouter, setSourceRouter] = useState<Record<string, unknown>>(() => cachedProviders?.sourceRouter || {});
    const [systemBase, setSystemBase] = useState<ConfigRegistryEnvelope<SystemBaseData> | null>(() => cachedSystemBase || null);
    const [loaded, setLoaded] = useState(() => Boolean(cachedProviders && cachedSystemBase));
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [query, setQuery] = useState("");
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState(false);

    const load = useCallback(async (force = false) => {
        if (!peekAdminJsonCache<SourceProvidersPayload>(SOURCE_PROVIDERS_URL) || !peekConfigDomain<SystemBaseData>("system-base")) {
            setLoading(true);
        }
        setError("");
        try {
            const [payload, configEnvelope] = await Promise.all([
                fetchAdminJson<SourceProvidersPayload>(SOURCE_PROVIDERS_URL, { force }),
                fetchConfigDomain<SystemBaseData>("system-base", { force }),
            ]);
            if (!payload.ok) throw new Error("source_provider_load_failed");
            setSystemBase(configEnvelope);
            setSourceRouter(payload.sourceRouter || {});
            setProviders((payload.providers || []).map((item) => mergeProviderConfig(item, configEnvelope)));
            setLoaded(true);
        } catch (loadError) {
            const message = loadError instanceof Error ? loadError.message : "source_provider_load_failed";
            setError(message);
        } finally {
            setLoading(false);
        }
    }, []);

    const handleToggle = () => {
        const nextExpanded = !expanded;
        setExpanded(nextExpanded);
        if (nextExpanded && !loaded && !loading) {
            void load();
        }
    };

    const filteredProviders = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (!needle) return providers;
        return providers.filter((provider) => {
            const text = [
                provider.id,
                provider.displayName,
                provider.region,
                provider.role,
                provider.authEnv,
                ...(provider.supports || []),
            ].join(" ").toLowerCase();
            return text.includes(needle);
        });
    }, [providers, query]);
    const enabledProviderCount = useMemo(
        () => providers.filter((provider) => provider.enabled).length,
        [providers],
    );

    const updateProvider = (providerId: string, patch: Partial<EditableProvider>) => {
        setProviders((current) => current.map((provider) => provider.id === providerId ? { ...provider, ...patch } : provider));
    };

    const handleSave = async () => {
        if (!systemBase) return;
        setSaving(true);
        try {
            const currentProviders = systemBase.data.webFetch?.providers || {};
            const nextProviders: Record<string, Record<string, unknown>> = { ...currentProviders };
            for (const provider of providers) {
                const previous = currentProviders[provider.id] || {};
                const next: Record<string, unknown> = {
                    ...previous,
                    enabled: provider.enabled,
                    authEnv: provider.authEnv,
                };
                if (provider.baseUrl) {
                    next.baseUrl = provider.baseUrl;
                } else {
                    delete next.baseUrl;
                }
                if (provider.apiKeyDraft.trim()) {
                    next.apiKey = provider.apiKeyDraft.trim();
                }
                nextProviders[provider.id] = next;
            }
            const nextWebFetch = {
                ...(systemBase.data.webFetch || {}),
                sourceRouter,
                providers: nextProviders,
            };
            const nextEnvelope = await saveConfigDomain<SystemBaseData>("system-base", {
                data: {
                    ...systemBase.data,
                    webFetch: nextWebFetch,
                },
            });
            setSystemBase(nextEnvelope);
            setProviders((current) => current.map((provider) => mergeProviderConfig({ ...provider, hasConfiguredKey: provider.hasConfiguredKey || Boolean(provider.apiKeyDraft.trim()) }, nextEnvelope)));
            toast({
                title: t("app.admin.research.sourceProviders.savedTitle"),
                description: t("app.admin.research.sourceProviders.savedDescription"),
            });
        } catch (saveError) {
            toast({
                title: t("app.admin.research.sourceProviders.saveFailedTitle"),
                description: saveError instanceof Error ? saveError.message : "source_provider_save_failed",
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    };

    return (
        <Card className="overflow-hidden rounded-3xl border-border bg-card/95 shadow-sm">
            <CardHeader className="space-y-0 p-0">
                <button
                    type="button"
                    data-testid="source-router-toggle"
                    className="flex w-full items-start justify-between gap-4 p-6 text-left transition-colors hover:bg-muted/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                    aria-expanded={expanded}
                    aria-controls="research-source-provider-content"
                    onClick={handleToggle}
                >
                    <div className="min-w-0 space-y-1">
                        <CardTitle className="flex items-center gap-2 text-base font-semibold text-foreground">
                            <Search className="h-4 w-4 text-sky-600 dark:text-sky-400" />
                            {t("app.admin.research.sourceProviders.title")}
                        </CardTitle>
                        <p className="line-clamp-2 text-sm leading-6 text-muted-foreground">
                            {t("app.admin.research.sourceProviders.description")}
                        </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                        <Badge
                            variant="outline"
                            className={cn(
                                "border-border bg-muted/35 text-muted-foreground",
                                error && "border-destructive/40 bg-destructive/10 text-destructive",
                            )}
                        >
                            {loading ? <Loader2 className="mr-1.5 h-3 w-3 animate-spin" /> : null}
                            {loading
                                ? t("app.admin.research.sourceProviders.loadingSummary")
                                : error
                                    ? t("app.admin.research.sourceProviders.loadFailedSummary")
                                    : loaded ? t("app.admin.research.sourceProviders.providerSummary", {
                                        enabled: enabledProviderCount,
                                        total: providers.length,
                                    }) : t("app.admin.research.sourceProviders.deferredSummary")}
                        </Badge>
                        <ChevronDown className={cn("h-5 w-5 text-muted-foreground transition-transform", expanded && "rotate-180")} />
                        <span className="sr-only">
                            {t(expanded ? "app.admin.research.sourceProviders.collapse" : "app.admin.research.sourceProviders.expand")}
                        </span>
                    </div>
                </button>
                {expanded ? (
                    <div className="space-y-3 border-t border-border/70 px-6 pb-6 pt-4">
                        <div className="flex flex-wrap items-center gap-2">
                            <Input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder={t("app.admin.research.sourceProviders.searchPlaceholder")}
                                className="h-9 w-full min-w-0 sm:w-64"
                            />
                            <Button type="button" variant="outline" size="sm" onClick={() => void load(true)} disabled={loading || saving}>
                                <RefreshCw className={cn("mr-2 h-4 w-4", loading && "animate-spin")} />
                                {t("app.admin.research.sourceProviders.refresh")}
                            </Button>
                            <Button type="button" size="sm" onClick={() => void handleSave()} disabled={loading || saving || !systemBase}>
                                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {t("app.admin.research.sourceProviders.save")}
                            </Button>
                        </div>
                        <div className="grid gap-2 text-xs text-muted-foreground md:grid-cols-3">
                            <div className="rounded-2xl border border-border bg-muted/30 px-3 py-2">
                                {t("app.admin.research.sourceProviders.networkRoute")}<span className="font-semibold text-foreground">{String(sourceRouter.defaultNetworkRoute || "auto")}</span>
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/30 px-3 py-2">
                                {t("app.admin.research.sourceProviders.cnPreferred")}{Array.isArray(sourceRouter.cnPreferred) ? sourceRouter.cnPreferred.join(" / ") : t("app.admin.research.sourceProviders.notConfigured")}
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/30 px-3 py-2">
                                {t("app.admin.research.sourceProviders.globalPreferred")}{Array.isArray(sourceRouter.globalPreferred) ? sourceRouter.globalPreferred.join(" / ") : t("app.admin.research.sourceProviders.notConfigured")}
                            </div>
                        </div>
                    </div>
                ) : null}
            </CardHeader>
            {expanded ? <CardContent id="research-source-provider-content" data-testid="source-router-content">
                {error ? (
                    <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">{error}</div>
                ) : null}
                {loading ? (
                    <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        {t("app.admin.research.sourceProviders.loading")}
                    </div>
                ) : (
                    <div className="grid gap-3 2xl:grid-cols-2">
                        {filteredProviders.map((provider) => {
                            const helpUrl = provider.credentialHelp?.url || "";
                            return (
                                <div key={provider.id} className="rounded-2xl border border-border bg-background/55 p-4 shadow-sm">
                                    <SettingToggleCard
                                        title={
                                            <div className="flex flex-wrap items-center gap-2">
                                                <span className="truncate text-sm font-semibold text-foreground">{provider.displayName}</span>
                                                <Badge variant={provider.implemented ? "secondary" : "outline"} className={provider.implemented ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "text-muted-foreground"}>
                                                    {provider.implemented ? t("app.admin.research.sourceProviders.implemented") : t("app.admin.research.sourceProviders.configSlot")}
                                                </Badge>
                                                <Badge variant="outline">{REGION_LABELS[provider.region] ? t(REGION_LABELS[provider.region]) : provider.region}</Badge>
                                                <Badge variant="outline">{ROLE_LABELS[provider.role] ? t(ROLE_LABELS[provider.role]) : provider.role}</Badge>
                                            </div>
                                        }
                                        description={`${provider.id} · ${provider.outputFormats.join(" / ")} · ${provider.supports.slice(0, 5).join(" / ")}`}
                                        checked={provider.enabled}
                                        onCheckedChange={(value) => updateProvider(provider.id, { enabled: Boolean(value) })}
                                        className="border-none bg-transparent hover:bg-transparent p-0 shadow-none"
                                    />
                                    <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                                        <label className="grid gap-1 text-xs font-medium text-muted-foreground">
                                            {t("app.admin.research.sourceProviders.envLabel")}
                                            <Input
                                                value={provider.authEnv}
                                                onChange={(event) => updateProvider(provider.id, { authEnv: event.target.value })}
                                                placeholder={t("app.admin.research.sourceProviders.envPlaceholder")}
                                                className="h-9"
                                            />
                                        </label>
                                        <label className="grid gap-1 text-xs font-medium text-muted-foreground">
                                            {t("app.admin.research.sourceProviders.apiKeyLabel")}
                                            <div className="flex gap-2">
                                                <Input
                                                    type="password"
                                                    value={provider.apiKeyDraft}
                                                    onChange={(event) => updateProvider(provider.id, { apiKeyDraft: event.target.value })}
                                                    placeholder={provider.hasConfiguredKey ? t("app.admin.research.sourceProviders.configuredKeyPlaceholder") : t("app.admin.research.sourceProviders.keyPlaceholder")}
                                                    className="h-9"
                                                />
                                                {helpUrl ? (
                                                    <Button type="button" variant="outline" size="icon" asChild>
                                                        <a href={helpUrl} target="_blank" rel="noreferrer" aria-label={provider.credentialHelp?.label || t("app.admin.research.sourceProviders.applyKey")}>
                                                            <ExternalLink className="h-4 w-4" />
                                                        </a>
                                                    </Button>
                                                ) : null}
                                            </div>
                                        </label>
                                    </div>
                                    {(provider.id === "searxng" || provider.baseUrl) ? (
                                        <label className="mt-3 grid gap-1 text-xs font-medium text-muted-foreground">
                                            {t("app.admin.research.sourceProviders.baseUrlLabel")}
                                            <Input
                                                value={provider.baseUrl}
                                                onChange={(event) => updateProvider(provider.id, { baseUrl: event.target.value })}
                                                placeholder={t("app.admin.research.sourceProviders.baseUrlPlaceholder")}
                                                className="h-9"
                                            />
                                        </label>
                                    ) : null}
                                    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                        <ShieldCheck className="h-3.5 w-3.5 text-muted-foreground/70" />
                                        <span>{t("app.admin.research.sourceProviders.proxy")}{String(provider.requiresProxy)}</span>
                                        <span>{t("app.admin.research.sourceProviders.cost")}{provider.costTier}</span>
                                        <span>{t("app.admin.research.sourceProviders.latency")}{provider.latencyTier}</span>
                                        {provider.supportsLoginProfile ? <span>{t("app.admin.research.sourceProviders.loginProfile")}</span> : null}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </CardContent> : null}
        </Card>
    );
}
