"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Loader2, MonitorSmartphone, RefreshCw } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { useT } from "@/components/providers/LocaleProvider";
import {
    fetchConfigDomain,
    peekConfigDomain,
    saveConfigDomain,
    type ConfigRegistryEnvelope,
} from "@/lib/config-registry";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";

type ModelOption = {
    id: string;
    modelRef?: string;
    providerId?: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { id?: string; name?: string };
    providerName?: string;
};

type ComputerUseData = {
    modelBindings: {
        plannerModel: string;
        visualJudgeModel: string;
        ocrAssistModel: string;
        candidateRerankerModel: string;
        fallbackRerankerModel?: string;
    };
    candidateRerankEnabled: boolean;
    memoryProfiles: {
        version?: number;
        apps?: Record<string, unknown>;
    };
    environmentPolicy: {
        runtimeFirst: boolean;
        interruptRequiresBlocker: boolean;
        noiseIgnoredByDefault: boolean;
    };
};

type RuntimeCapabilityEntry = {
    kind: string;
    displayName?: string;
    summary?: string;
    availability?: string;
    policy?: {
        enabled?: boolean;
        autoRoute?: boolean;
        exposeDirectTools?: boolean;
    };
};

function runtimeCapabilityCanBeEnabled(capability: RuntimeCapabilityEntry | null) {
    return capability?.availability === "installed" || capability?.availability === "disabled_by_policy";
}

type CapabilityFacet = {
    key: string;
    status?: string;
    available?: boolean;
    validationLevel?: string;
};

type CapabilityPlatform = {
    displayPlatform?: string;
    platform?: string;
    currentHost?: boolean;
    facets?: CapabilityFacet[];
};

type ComputerUseAvailability = {
    platform?: string;
    backend?: string;
    available?: boolean | null;
    environmentProbe?: {
        state?: "fresh" | "refreshing" | "failed" | string;
        stale?: boolean;
        refreshing?: boolean;
        error?: string | null;
    };
    details?: {
        capabilityTruth?: {
            currentPlatform?: string;
            platforms?: Record<string, CapabilityPlatform>;
            platformParity?: {
                platforms?: Record<string, {
                    platform?: string;
                    status?: string;
                    driverContract?: string;
                    expectedDependencies?: string[];
                    actionChecklist?: string[];
                    turiXRefs?: string[];
                }>;
            };
            browserLaneTruth?: Record<string, unknown>;
            knownGaps?: Array<{ code?: string; summary?: string; impact?: string }>;
            portableChecklist?: string[];
            screenWakePolicy?: Record<string, unknown>;
        };
        builtInPlaybookSeeds?: Array<{ id?: string; status?: string; domain?: string; operation?: string; preferredLane?: string }>;
    };
};

type RuntimeFeaturePackEntry = {
    id?: string;
    status?: string;
    restartRequired?: boolean;
};

type FeaturePackGateState = "unknown" | "missing" | "restart_required" | "ready";

function computerUseProbeReady(availability: ComputerUseAvailability | null) {
    return availability?.available === true
        && availability.environmentProbe?.state === "fresh"
        && availability.environmentProbe?.stale !== true
        && availability.environmentProbe?.refreshing !== true;
}

export default function DesktopAutomationPage() {
    const t = useT();
    const [initialState] = useState(() => {
        const envelope = peekConfigDomain<ComputerUseData>("computer-use") ?? null;
        const models = peekAdminJsonCache<ModelOption[]>("/api/models");
        const capabilitySnapshot = peekAdminJsonCache<Record<string, unknown>>("/api/runtime-capabilities");
        const runtimes = Array.isArray(capabilitySnapshot?.runtimes) ? capabilitySnapshot.runtimes : [];
        const featurePacks = peekAdminJsonCache<{ packs?: RuntimeFeaturePackEntry[] }>("/api/runtime-feature-packs");
        const packs = Array.isArray(featurePacks?.packs) ? featurePacks.packs : [];
        return {
            envelope,
            models: Array.isArray(models) ? models : [],
            runtimeCapability: runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "computer_use") || null,
            availability: peekAdminJsonCache<ComputerUseAvailability>("/api/computer-use/availability") ?? null,
            featurePack: featurePacks !== undefined
                ? packs.find((item) => item.id === "computer_use_desktop") || { status: "not_installed" }
                : null,
        };
    });
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<ComputerUseData> | null>(initialState.envelope);
    const [models, setModels] = useState<ModelOption[]>(initialState.models);
    const [runtimeCapability, setRuntimeCapability] = useState<RuntimeCapabilityEntry | null>(initialState.runtimeCapability);
    const [availability, setAvailability] = useState<ComputerUseAvailability | null>(initialState.availability);
    const [availabilityChecked, setAvailabilityChecked] = useState(false);
    const [availabilityRefreshKey, setAvailabilityRefreshKey] = useState(0);
    const [loading, setLoading] = useState(!initialState.envelope);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [runtimeSaving, setRuntimeSaving] = useState(false);
    const [featurePack, setFeaturePack] = useState<RuntimeFeaturePackEntry | null>(initialState.featurePack);

    const loadData = async (force = false) => {
        try {
            const [config, modelsResponse, capabilitySnapshot, featurePacks] = await Promise.all([
                fetchConfigDomain<ComputerUseData>("computer-use", { force }),
                fetchAdminJson<ModelOption[]>("/api/models", { force }),
                fetchAdminJson<Record<string, unknown>>("/api/runtime-capabilities", { force }).catch(() => null),
                fetchAdminJson<{ packs?: RuntimeFeaturePackEntry[] }>("/api/runtime-feature-packs", { force }).catch(() => null),
            ]);
            setEnvelope(config);
            setModels(Array.isArray(modelsResponse) ? modelsResponse : []);
            setAvailabilityChecked(false);
            setAvailabilityRefreshKey((current) => current + 1);
            const runtimes = Array.isArray(capabilitySnapshot?.runtimes) ? capabilitySnapshot.runtimes : [];
            setRuntimeCapability(runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "computer_use") || null);
            if (featurePacks) {
                const packs = Array.isArray(featurePacks.packs) ? featurePacks.packs : [];
                setFeaturePack(packs.find((item) => item.id === "computer_use_desktop") || { status: "not_installed" });
            } else {
                setFeaturePack(null);
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    useEffect(() => {
        if (availabilityRefreshKey === 0) return;
        let active = true;
        let pollTimer: number | undefined;
        const availabilityUrl = availabilityRefreshKey > 1
            ? "/api/computer-use/availability?refresh=true"
            : "/api/computer-use/availability";
        const loadAvailability = (attempt = 0) => {
            const requestUrl = attempt > 0 ? "/api/computer-use/availability" : availabilityUrl;
            void fetchAdminJson<ComputerUseAvailability>(requestUrl, {
                force: availabilityRefreshKey > 1 || attempt > 0,
                ttlMs: 30_000,
            }).then((computerAvailability) => {
                if (!active) return;
                setAvailability(computerAvailability || null);
                setAvailabilityChecked(true);
                if (computerAvailability?.environmentProbe?.refreshing && attempt < 20) {
                    pollTimer = window.setTimeout(() => loadAvailability(attempt + 1), 2_000);
                }
            }).catch(() => {
                if (active) {
                    setAvailability(null);
                    setAvailabilityChecked(true);
                }
            });
        };
        const timer = window.setTimeout(loadAvailability, 1_000);
        return () => {
            active = false;
            window.clearTimeout(timer);
            if (pollTimer !== undefined) window.clearTimeout(pollTimer);
        };
    }, [availabilityRefreshKey]);

    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "").toUpperCase())),
        [models]
    );
    const rerankModels = useMemo(
        () => models.filter((model) => ["RERANK", "RERANKER"].includes((model.type || "").toUpperCase())),
        [models]
    );
    const capabilityTruth = availability?.details?.capabilityTruth || null;
    const truthPlatforms = useMemo(
        () => Object.entries(capabilityTruth?.platforms || {}).map(([key, value]) => ({ key, ...value })),
        [capabilityTruth]
    );
    const currentPlatformTruth = truthPlatforms.find((item) => item.currentHost) || truthPlatforms[0];
    const parityPlatforms = useMemo(
        () => Object.entries(capabilityTruth?.platformParity?.platforms || {}).map(([key, value]) => ({ key, ...value })),
        [capabilityTruth]
    );
    const builtInPlaybooks = availability?.details?.builtInPlaybookSeeds || [];
    const featurePackState = useMemo<FeaturePackGateState>(() => {
        if (!featurePack) return "unknown";
        if (featurePack.status !== "installed") return "missing";
        if (featurePack.restartRequired !== false || !runtimeCapabilityCanBeEnabled(runtimeCapability)) {
            return "restart_required";
        }
        if (!availabilityChecked || !computerUseProbeReady(availability)) return "unknown";
        return "ready";
    }, [availability, availabilityChecked, featurePack, runtimeCapability]);
    const availabilityNotice = useMemo(() => {
        const state = availability?.environmentProbe?.state
            || (availability ? "fresh" : availabilityChecked ? "failed" : "refreshing");
        const stale = Boolean(availability?.environmentProbe?.stale);
        if (state === "failed") {
            return {
                title: "app.admin.dashboard.desktop.automation.availability.failedTitle",
                description: stale
                    ? "app.admin.dashboard.desktop.automation.availability.failedStaleDescription"
                    : "app.admin.dashboard.desktop.automation.availability.failedUnknownDescription",
                tone: "warning" as const,
            };
        }
        if (state === "refreshing") {
            return {
                title: stale
                    ? "app.admin.dashboard.desktop.automation.availability.refreshingStaleTitle"
                    : "app.admin.dashboard.desktop.automation.availability.refreshingTitle",
                description: stale
                    ? "app.admin.dashboard.desktop.automation.availability.refreshingStaleDescription"
                    : "app.admin.dashboard.desktop.automation.availability.refreshingDescription",
                tone: "info" as const,
            };
        }
        return null;
    }, [availability, availabilityChecked]);
    const availabilityFailed = availabilityChecked
        && (!availability || availability.environmentProbe?.state === "failed");
    const retryAvailability = () => {
        setAvailabilityChecked(false);
        setAvailabilityRefreshKey((current) => current + 1);
    };
    const statusLabel = (status?: string) => {
        const normalized = String(status || "unknown");
        return t(`app.admin.dashboard.desktop.automation.capabilityTruth.status.${normalized}`);
    };
    const facetLabel = (key?: string) => {
        const normalized = String(key || "unknown");
        return t(`app.admin.dashboard.desktop.automation.capabilityTruth.facet.${normalized}`);
    };
    const statusClassName = (status?: string) => {
        const normalized = String(status || "");
        if (normalized === "real_host_passed") return "border-emerald-200 bg-emerald-50 text-emerald-700";
        if (normalized === "blocked_by_permission" || normalized.startsWith("blocked_by")) return "border-amber-200 bg-amber-50 text-amber-700";
        if (normalized === "unsupported") return "border-border bg-muted text-muted-foreground";
        return "border-sky-200 bg-sky-50 text-sky-700";
    };

    const updateBinding = (key: keyof ComputerUseData["modelBindings"], value: string) => {
        if (!envelope) return;
        setEnvelope({
            ...envelope,
            data: {
                ...envelope.data,
                modelBindings: {
                    ...envelope.data.modelBindings,
                    [key]: value === "__empty__" ? "" : value,
                },
            },
        });
    };

    const handleSave = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<ComputerUseData>("computer-use", { data: envelope.data });
            setEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } finally {
            setSaving(false);
        }
    };

    const setRuntimeEnabled = async (enabled: boolean) => {
        if (enabled && featurePackState !== "ready") {
            if (featurePackState === "missing") {
                window.dispatchEvent(new Event("v8os:open-feature-packs"));
            } else {
                void loadData(true);
            }
            return;
        }
        setRuntimeSaving(true);
        try {
            await fetch("/api/runtime-capabilities/computer_use/policy", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    enabled,
                    autoRoute: enabled,
                    exposeDirectTools: enabled,
                    notes: enabled ? "Admin desktop-automation enabled" : "Admin desktop-automation disabled",
                }),
            });
            await loadData(true);
        } finally {
            setRuntimeSaving(false);
        }
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={"app.admin.dashboard.desktop.automation.page.k20b2ed6e"}
                description={"app.admin.dashboard.desktop.automation.page.k0269b376"}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MonitorSmartphone className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.desktop.automation.page.k6010e1ed")}
                        </Button>
                    </div>
                }
            />

            <StatusNotice
                title={"app.admin.dashboard.desktop.automation.page.k528406eb"}
                tone="info"
            />

            {availabilityNotice ? (
                <div className="space-y-2">
                    <StatusNotice
                        title={availabilityNotice.title}
                        description={availabilityNotice.description}
                        tone={availabilityNotice.tone}
                    />
                    {availabilityFailed ? (
                        <Button type="button" variant="outline" size="sm" onClick={retryAvailability}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.desktop.automation.availability.retry")}
                        </Button>
                    ) : null}
                </div>
            ) : null}

            {featurePackState === "missing" ? (
                <StatusNotice
                    title={"app.admin.dashboard.desktop.automation.featurePackMissingTitle"}
                    description={"app.admin.dashboard.desktop.automation.featurePackMissingDescription"}
                    tone="warning"
                />
            ) : null}

            {featurePackState === "restart_required" ? (
                <StatusNotice
                    title={"components.layout.Topbar.featurePackRestartRequired"}
                    tone="warning"
                />
            ) : null}

            {featurePackState === "unknown" && !availabilityNotice ? (
                <StatusNotice
                    title={"app.admin.dashboard.desktop.automation.availability.failedTitle"}
                    description={"app.admin.dashboard.desktop.automation.availability.failedUnknownDescription"}
                    tone="warning"
                />
            ) : null}

            <ConfigCard title={"app.admin.dashboard.desktop.automation.page.kb6443896"} description={"app.admin.dashboard.desktop.automation.page.kc064340e"}>
                <div className="space-y-3">
                    <div className="grid gap-3 text-sm text-muted-foreground sm:grid-cols-2">
                        <div className="rounded-2xl border border-border bg-muted/80 p-4">
                            <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.desktop.automation.page.k73b0e79c")}</div>
                            <div className="mt-1 text-base font-semibold text-foreground">
                                {runtimeCapability?.policy?.enabled === false ? t("app.admin.dashboard.desktop.automation.page.kc6ff9900") : t("app.admin.dashboard.desktop.automation.page.kdb6c0cc1")}
                            </div>
                            <div className="mt-2 text-xs text-muted-foreground">
                                {runtimeCapability?.displayName || t("app.admin.dashboard.desktop.automation.page.k8e3b4847")} · {runtimeCapability?.summary || t("app.admin.dashboard.desktop.automation.page.k4039664e")}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-border bg-muted/80 p-4 text-xs leading-6 text-muted-foreground">
                            <div>{t("app.admin.dashboard.desktop.automation.page.k660c8c15")}{runtimeCapability?.policy?.autoRoute === false ? t("app.admin.dashboard.desktop.automation.page.k574ff3b2") : t("app.admin.dashboard.desktop.automation.page.k85549844")}</div>
                            <div>{t("app.admin.dashboard.desktop.automation.page.k320d306e")}{runtimeCapability?.policy?.exposeDirectTools === false ? t("app.admin.dashboard.desktop.automation.page.k574ff3b2") : t("app.admin.dashboard.desktop.automation.page.k85549844")}</div>
                            <div>{t("app.admin.dashboard.desktop.automation.page.k77ed4e5b")}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <Button
                            type="button"
                            onClick={() => void setRuntimeEnabled(true)}
                            disabled={runtimeSaving || (featurePackState === "ready"
                                ? runtimeCapability?.policy?.enabled !== false
                                : featurePackState !== "missing")}
                        >
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.desktop.automation.page.kf0adb2b0")}
                        </Button>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => void setRuntimeEnabled(false)}
                            disabled={runtimeSaving || runtimeCapability?.policy?.enabled === false}
                        >
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.desktop.automation.page.k415e3223")}
                        </Button>
                    </div>
                </div>
            </ConfigCard>

            <ConfigCard
                title={"app.admin.dashboard.desktop.automation.capabilityTruth.title"}
                description={"app.admin.dashboard.desktop.automation.capabilityTruth.description"}
            >
                <div className="space-y-4">
                    <div className="grid gap-3 lg:grid-cols-[1.5fr_1fr]">
                        <div className="rounded-2xl border border-border bg-muted/80 p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.currentPlatform")}</div>
                                    <div className="mt-1 text-lg font-semibold text-foreground">
                                        {currentPlatformTruth?.displayPlatform || availability?.platform || t("app.admin.dashboard.desktop.automation.capabilityTruth.unknown")}
                                    </div>
                                </div>
                                <span className="rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
                                    {availability?.backend || t("app.admin.dashboard.desktop.automation.capabilityTruth.unknown")}
                                </span>
                            </div>
                            <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                                {(currentPlatformTruth?.facets || []).map((facet) => (
                                    <div key={facet.key} className="rounded-xl border border-white bg-card px-3 py-2 shadow-sm">
                                        <div className="text-xs font-medium text-foreground">{facetLabel(facet.key)}</div>
                                        <div className={`mt-1 inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClassName(facet.status)}`}>
                                            {statusLabel(facet.status)}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div className="space-y-3">
                            <div className="rounded-2xl border border-border bg-muted/80 p-4">
                                <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.browserLane")}</div>
                                <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${statusClassName(String(capabilityTruth?.browserLaneTruth?.status || ""))}`}>
                                    {statusLabel(String(capabilityTruth?.browserLaneTruth?.status || "unknown"))}
                                </div>
                                {capabilityTruth?.browserLaneTruth?.reason ? (
                                    <p className="mt-2 text-xs leading-5 text-muted-foreground">{String(capabilityTruth.browserLaneTruth.reason)}</p>
                                ) : null}
                                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                                    {t("app.admin.dashboard.desktop.automation.capabilityTruth.playwright")}
                                    {capabilityTruth?.browserLaneTruth?.playwrightAvailable
                                        ? t("app.admin.dashboard.desktop.automation.page.k85549844")
                                        : t("app.admin.dashboard.desktop.automation.page.k574ff3b2")}
                                </p>
                                {capabilityTruth?.browserLaneTruth?.defaultUserDataDir ? (
                                    <p className="mt-1 truncate text-xs leading-5 text-muted-foreground" title={String(capabilityTruth.browserLaneTruth.defaultUserDataDir)}>
                                        {t("app.admin.dashboard.desktop.automation.capabilityTruth.browserProfile")}
                                        {String(capabilityTruth.browserLaneTruth.defaultUserDataDir)}
                                    </p>
                                ) : null}
                                {capabilityTruth?.browserLaneTruth?.targetPort ? (
                                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                        {t("app.admin.dashboard.desktop.automation.capabilityTruth.debugPort")}
                                        {String(capabilityTruth.browserLaneTruth.targetPort)}
                                    </p>
                                ) : null}
                            </div>
                            <div className="rounded-2xl border border-border bg-muted/80 p-4">
                                <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.screenWake")}</div>
                                <div className="mt-2 text-sm font-semibold text-foreground">
                                    {t("app.admin.dashboard.desktop.automation.capabilityTruth.spaceWake")}
                                </div>
                                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                    {t("app.admin.dashboard.desktop.automation.capabilityTruth.screenWakeDetail")}
                                </p>
                            </div>
                        </div>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-2">
                        <div className="rounded-2xl border border-border bg-card p-4">
                            <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.portableChecklist")}</div>
                            <div className="mt-2 flex flex-wrap gap-2">
                                {(capabilityTruth?.portableChecklist || []).slice(0, 12).map((item) => (
                                    <span key={item} className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
                                        {item}
                                    </span>
                                ))}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-border bg-card p-4">
                            <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.knownGaps")}</div>
                            <div className="mt-2 space-y-2">
                                {(capabilityTruth?.knownGaps || []).length ? (
                                    (capabilityTruth?.knownGaps || []).map((gap) => (
                                        <div key={gap.code || gap.summary} className="rounded-xl bg-muted/50 px-3 py-2 text-xs leading-5 text-muted-foreground">
                                            <span className="font-semibold text-foreground">{gap.code}</span>
                                            {gap.summary ? ` · ${gap.summary}` : ""}
                                        </div>
                                    ))
                                ) : (
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.noKnownGaps")}</div>
                                )}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-border bg-card p-4">
                            <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.parityPackage")}</div>
                            <div className="mt-2 space-y-2">
                                {parityPlatforms.slice(0, 4).map((platform) => (
                                    <div key={platform.key} className="rounded-xl bg-muted/50 px-3 py-2 text-xs leading-5 text-muted-foreground">
                                        <div className="font-semibold text-foreground">
                                            {platform.platform || platform.key} · {statusLabel(platform.status)}
                                        </div>
                                        <div className="truncate">{platform.driverContract || ""}</div>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-border bg-card p-4">
                            <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.playbooks")}</div>
                            <div className="mt-2 space-y-2">
                                {builtInPlaybooks.length ? (
                                    builtInPlaybooks.map((playbook) => (
                                        <div key={playbook.id} className="rounded-xl bg-muted/50 px-3 py-2 text-xs leading-5 text-muted-foreground">
                                            <span className="font-semibold text-foreground">{playbook.id}</span>
                                            {playbook.preferredLane ? ` · ${playbook.preferredLane}` : ""}
                                        </div>
                                    ))
                                ) : (
                                    <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.desktop.automation.capabilityTruth.noPlaybooks")}</div>
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
                        {truthPlatforms.map((platform) => (
                            <div key={platform.key} className="rounded-xl border border-border bg-muted/80 px-3 py-2">
                                <div className="font-semibold text-foreground">{platform.displayPlatform || platform.key}</div>
                                <div>{platform.currentHost ? t("app.admin.dashboard.desktop.automation.capabilityTruth.realHost") : t("app.admin.dashboard.desktop.automation.capabilityTruth.portableOnly")}</div>
                            </div>
                        ))}
                    </div>
                </div>
            </ConfigCard>

            <div className="grid gap-4 xl:grid-cols-2">
                <ConfigCard title={"app.admin.dashboard.desktop.automation.page.keb697d9b"} description={"app.admin.dashboard.desktop.automation.page.k2a55c8b3"}>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <Label>{t("app.admin.dashboard.desktop.automation.page.k9d1ed51e")}</Label>
                            <AdminHoverInfo
                                content={
                                    <div className="space-y-1.5 text-xs leading-5">
                                        <div className="font-semibold text-foreground">{t("app.admin.dashboard.desktop.automation.page.kf558439c")}</div>
                                        <div>
                                            {t("app.admin.dashboard.desktop.automation.page.ke2e6c721")}
                                            {" "}
                                            <Link href="/admin/supervisor#vision-media-model" className="font-medium text-sky-700 underline underline-offset-2 hover:text-sky-800">
                                                {t("app.admin.dashboard.desktop.automation.page.kf45c6152")}
                                            </Link>
                                            {" "}
                                            {t("app.admin.dashboard.desktop.automation.page.k2a90dceb")}
                                        </div>
                                        <div className="mt-1 text-muted-foreground">
                                            {t("app.admin.dashboard.desktop.automation.page.k091d7083")}{envelope.data.modelBindings.ocrAssistModel || t("app.admin.dashboard.desktop.automation.page.k594ee6bf")}
                                        </div>
                                    </div>
                                }
                                panelClassName="max-w-xs"
                            >
                                <span className="cursor-help text-muted-foreground/80 hover:text-muted-foreground">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor" className="h-4 w-4">
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9 5.25h.008v.008H12v-.008z" />
                                    </svg>
                                </span>
                            </AdminHoverInfo>
                        </div>
                        <ModelSelect
                            models={llmModels}
                            value={envelope.data.modelBindings.plannerModel || "__empty__"}
                            emptyLabel={t("app.admin.dashboard.desktop.automation.page.k54745147")}
                            placeholder={t("app.admin.dashboard.desktop.automation.page.k54745147")}
                            onValueChange={(value) => updateBinding("plannerModel", value)}
                        />
                    </div>
                </ConfigCard>

                <ConfigCard title={"app.admin.dashboard.desktop.automation.page.ke9dbede4"} description={"app.admin.dashboard.desktop.automation.page.k47ac837b"}>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.desktop.automation.page.k428ef950")}</Label>
                            <ModelSelect
                                models={llmModels}
                                value={envelope.data.modelBindings.visualJudgeModel || "__empty__"}
                                emptyLabel={t("app.admin.dashboard.desktop.automation.page.k54745147")}
                                placeholder={t("app.admin.dashboard.desktop.automation.page.k54745147")}
                                onValueChange={(value) => updateBinding("visualJudgeModel", value)}
                            />
                    </div>
                        <SettingToggleCard
                            title={t("app.admin.dashboard.desktop.automation.page.kb8f544bf")}
                            description={t("app.admin.dashboard.desktop.automation.page.kdc8cc070")}
                            checked={Boolean(envelope.data.candidateRerankEnabled)}
                            onCheckedChange={(checked) =>
                                setEnvelope({
                                    ...envelope,
                                    data: {
                                        ...envelope.data,
                                        candidateRerankEnabled: checked,
                                    },
                                })
                            }
                            className="rounded-2xl border-border bg-muted/80 p-4"
                        />
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktop.automation.page.kfef16620")}</Label>
                        <ModelSelect
                            models={rerankModels}
                            value={envelope.data.modelBindings.candidateRerankerModel || "__empty__"}
                            emptyLabel={t("app.admin.dashboard.desktop.automation.page.k39830d18")}
                            placeholder={t("app.admin.dashboard.desktop.automation.page.k39830d18")}
                            onValueChange={(value) => updateBinding("candidateRerankerModel", value)}
                        />
                        <p className="text-xs leading-5 text-muted-foreground">
                            {t("app.admin.dashboard.desktop.automation.page.k59f13024")}{envelope.data.modelBindings.fallbackRerankerModel || t("app.admin.dashboard.desktop.automation.page.k54745147")}{t("app.admin.dashboard.desktop.automation.page.k75b279f7")}
                        </p>
                    </div>
                </ConfigCard>
            </div>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />
        </AdminPageShell>
    );
}
