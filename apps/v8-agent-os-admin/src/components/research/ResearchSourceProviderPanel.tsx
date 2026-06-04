"use client";

import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2, RefreshCw, Save, Search, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
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
    const [providers, setProviders] = useState<EditableProvider[]>([]);
    const [sourceRouter, setSourceRouter] = useState<Record<string, unknown>>({});
    const [systemBase, setSystemBase] = useState<ConfigRegistryEnvelope<SystemBaseData> | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [query, setQuery] = useState("");
    const [error, setError] = useState("");

    const load = async () => {
        setLoading(true);
        setError("");
        try {
            const [providersResponse, configEnvelope] = await Promise.all([
                fetch("/api/research-runtime?view=source-providers", { cache: "no-store" }),
                fetchConfigDomain<SystemBaseData>("system-base"),
            ]);
            const providerPayload = (await providersResponse.json().catch(() => ({}))) as SourceProvidersPayload | { detail?: string; error?: string };
            if (!providersResponse.ok || !(providerPayload as SourceProvidersPayload).ok) {
                throw new Error((providerPayload as { detail?: string; error?: string }).detail || (providerPayload as { error?: string }).error || "source_provider_load_failed");
            }
            const payload = providerPayload as SourceProvidersPayload;
            setSystemBase(configEnvelope);
            setSourceRouter(payload.sourceRouter || {});
            setProviders((payload.providers || []).map((item) => mergeProviderConfig(item, configEnvelope)));
        } catch (loadError) {
            const message = loadError instanceof Error ? loadError.message : "source_provider_load_failed";
            setError(message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
    }, []);

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
        <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
            <CardHeader className="space-y-3">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
                    <div className="space-y-1">
                        <CardTitle className="flex items-center gap-2 text-base font-semibold text-slate-900">
                            <Search className="h-4 w-4 text-sky-600" />
                            {t("app.admin.research.sourceProviders.title")}
                        </CardTitle>
                        <p className="text-sm leading-6 text-slate-500">
                            {t("app.admin.research.sourceProviders.description")}
                        </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                        <Input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder={t("app.admin.research.sourceProviders.searchPlaceholder")}
                            className="h-9 w-full min-w-0 sm:w-64"
                        />
                        <Button type="button" variant="outline" size="sm" onClick={() => void load()} disabled={loading || saving}>
                            <RefreshCw className={cn("mr-2 h-4 w-4", loading && "animate-spin")} />
                            {t("app.admin.research.sourceProviders.refresh")}
                        </Button>
                        <Button type="button" size="sm" onClick={() => void handleSave()} disabled={loading || saving || !systemBase}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.research.sourceProviders.save")}
                        </Button>
                    </div>
                </div>
                <div className="grid gap-2 text-xs text-slate-500 md:grid-cols-3">
                    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2">
                        {t("app.admin.research.sourceProviders.networkRoute")}<span className="font-semibold text-slate-800">{String(sourceRouter.defaultNetworkRoute || "auto")}</span>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2">
                        {t("app.admin.research.sourceProviders.cnPreferred")}{Array.isArray(sourceRouter.cnPreferred) ? sourceRouter.cnPreferred.join(" / ") : t("app.admin.research.sourceProviders.notConfigured")}
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2">
                        {t("app.admin.research.sourceProviders.globalPreferred")}{Array.isArray(sourceRouter.globalPreferred) ? sourceRouter.globalPreferred.join(" / ") : t("app.admin.research.sourceProviders.notConfigured")}
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                {error ? (
                    <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>
                ) : null}
                {loading ? (
                    <div className="flex h-32 items-center justify-center text-sm text-slate-500">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        {t("app.admin.research.sourceProviders.loading")}
                    </div>
                ) : (
                    <div className="grid gap-3 2xl:grid-cols-2">
                        {filteredProviders.map((provider) => {
                            const helpUrl = provider.credentialHelp?.url || "";
                            return (
                                <div key={provider.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                                    <SettingToggleCard
                                        title={
                                            <div className="flex flex-wrap items-center gap-2">
                                                <span className="truncate text-sm font-semibold text-slate-900">{provider.displayName}</span>
                                                <Badge variant={provider.implemented ? "secondary" : "outline"} className={provider.implemented ? "bg-emerald-50 text-emerald-700" : "text-slate-500"}>
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
                                        <label className="grid gap-1 text-xs font-medium text-slate-600">
                                            {t("app.admin.research.sourceProviders.envLabel")}
                                            <Input
                                                value={provider.authEnv}
                                                onChange={(event) => updateProvider(provider.id, { authEnv: event.target.value })}
                                                placeholder={t("app.admin.research.sourceProviders.envPlaceholder")}
                                                className="h-9"
                                            />
                                        </label>
                                        <label className="grid gap-1 text-xs font-medium text-slate-600">
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
                                        <label className="mt-3 grid gap-1 text-xs font-medium text-slate-600">
                                            {t("app.admin.research.sourceProviders.baseUrlLabel")}
                                            <Input
                                                value={provider.baseUrl}
                                                onChange={(event) => updateProvider(provider.id, { baseUrl: event.target.value })}
                                                placeholder={t("app.admin.research.sourceProviders.baseUrlPlaceholder")}
                                                className="h-9"
                                            />
                                        </label>
                                    ) : null}
                                    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                        <ShieldCheck className="h-3.5 w-3.5 text-slate-400" />
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
            </CardContent>
        </Card>
    );
}
