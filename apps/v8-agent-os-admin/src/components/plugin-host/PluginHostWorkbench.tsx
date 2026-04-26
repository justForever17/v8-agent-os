"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { type ConfigRegistryEnvelope, fetchConfigDomain, saveConfigDomain } from "@/lib/config-registry";
type HostMode = "managed_local" | "external";
type RuntimeConfig = {
    enabled: boolean;
    scanOnStartup: boolean;
    hostMode: HostMode;
    allowedFamilies: string[];
    managedLocal: {
        rootDir: string;
        toolingRoot: string;
        launcherPath: string;
        autoStart: boolean;
    };
    externalHost: {
        baseUrl: string;
        gatewayBaseUrl: string;
        authToken: string;
    };
};
type Snapshot = {
    runtimeConfig?: RuntimeConfig;
    startupState?: "cold" | "refreshing" | "ready" | "error";
    snapshotFreshness?: "cached" | "live";
    refreshInFlight?: boolean;
    lastRefreshAt?: string | null;
    lastLiveRefreshAt?: string | null;
    lastDeepRefreshAt?: string | null;
    lastRefreshError?: string | null;
    controlSurface?: {
        dashboardUrl?: string | null;
        configUrl?: string | null;
        docsUrl?: string | null;
    };
    summary?: {
        pluginCount?: number;
        activeCount?: number;
        channelPluginCount?: number;
    };
    hostSurface?: {
        inboundOwnership?: string | null;
        handoffReady?: boolean;
        handoffDrift?: boolean;
        bridgeReady?: boolean;
        bridgePluginId?: string | null;
        managedChannels?: string[] | null;
        installProvenance?: string | null;
        installTrusted?: boolean;
        managedChannelsSource?: string | null;
        configSource?: string | null;
        refreshMode?: string | null;
        resolvedStateDir?: string | null;
        gatewayBaseUrl?: string | null;
        v8InboundUrl?: string | null;
        bridgeStatusSource?: string | null;
        bridgeStatusObservedAt?: string | null;
        bridgeStatusMs?: number | null;
        bridgeStatusError?: string | null;
        bridgeStatusStale?: boolean;
        handoffConfigured?: boolean;
        claimEnabled?: boolean;
        lastClaimAt?: string | null;
        lastClaimAttemptAt?: string | null;
        lastClaimOutcome?: string | null;
        lastClaimDeclineReason?: string | null;
        lastClaimChannel?: string | null;
        lastClaimConversation?: string | null;
        lastClaimMessageId?: string | null;
        lastClaimAccountId?: string | null;
        lastClaimPayloadShape?: Record<string, unknown> | null;
        expectedBridgeClaimMissed?: boolean;
        pluginsAllowConfigured?: boolean | null;
        pluginsAllow?: string[] | null;
        pluginsAllowExpected?: string[] | null;
        pluginProvenanceWarnings?: Array<{
            kind?: string | null;
            level?: string | null;
            title?: string | null;
            description?: string | null;
            pluginId?: string | null;
            pluginIds?: string[] | null;
            source?: string | null;
            fields?: string[] | null;
        }> | null;
        fieldContractWarnings?: Array<{
            kind?: string | null;
            level?: string | null;
            title?: string | null;
            description?: string | null;
            fields?: string[] | null;
        }> | null;
        bridgeDoctorSummary?: {
            status?: string | null;
            criticalCount?: number | null;
            warningCount?: number | null;
            okCount?: number | null;
            title?: string | null;
            description?: string | null;
            checkedAt?: string | null;
        } | null;
        bridgeDoctorChecks?: Array<{
            key?: string | null;
            status?: string | null;
            title?: string | null;
            description?: string | null;
            details?: string | null;
            data?: Record<string, unknown> | null;
        }> | null;
        lastInboundHandoffAt?: string | null;
        lifecycleAuthority?: string | null;
        cliSource?: string | null;
        toolingMode?: string | null;
        toolingEntry?: string | null;
        launcherSource?: string | null;
        launcherMissing?: boolean;
        outboundReady?: boolean;
        gatewayHealth?: {
            runtime?: {
                status?: string | null;
                detail?: string | null;
            };
            rpc?: {
                ok?: boolean;
                error?: string | null;
            };
        } | null;
        recentInboundProof?: {
            stage?: string | null;
            reason?: string | null;
            runId?: string | null;
            pushRunId?: string | null;
            pushStatus?: string | null;
            inboundObservedAt?: string | null;
        } | null;
    };
    plugins?: Array<{
        pluginId: string;
        displayName?: string | null;
        pluginType?: string | null;
        setupState?: string | null;
        activationState?: string | null;
        lifecycleState?: string | null;
        healthState?: string | null;
        supportTier?: string | null;
        familyAdapterReady?: boolean;
        onboardingCompleted?: boolean;
        unavailableReasons?: string[] | null;
        transportCapabilities?: {
            chatTypes?: string[] | null;
            groupSupported?: boolean;
            onboardingType?: string | null;
        } | null;
        channelSurface?: {
            channelIds?: string[] | null;
            registeredAccounts?: string[] | null;
            configured?: boolean;
            liveInboundProven?: boolean;
            replyDelivered?: boolean;
            evidence?: string[] | null;
        } | null;
    }>;
};
type DomainData = {
    config: RuntimeConfig;
    snapshot?: Snapshot;
};
type SysModel = {
    id: string;
    modelRef?: string;
    providerId?: string;
    modelId: string;
    name: string;
    type: string;
    provider?: {
        id?: string;
        name?: string;
    };
    providerName?: string;
};
type ExtensionsConfigData = {
    prefilterPolicy?: {
        enabled?: boolean;
        mode?: string;
    };
    modelBindings?: {
        prefilterModel?: string;
    };
};
type BridgeToolEntry = {
    canonicalName?: string;
    pluginId?: string | null;
    toolName?: string;
    label?: string;
    description?: string;
    source?: string;
    allowed?: boolean;
};
type BridgeToolSelection = {
    mode?: string | null;
    modelId?: string | null;
    role?: string | null;
    reason?: string | null;
    prefilterTimedOut?: boolean | null;
    prefilterCacheHit?: boolean | null;
    prefilterDurationMs?: number | null;
    poolSize?: number;
    inventorySize?: number;
    callableSize?: number;
    timingsMs?: Record<string, number> | null;
};
type BridgeToolCatalog = {
    selection?: BridgeToolSelection | null;
    exposure?: BridgeToolEntry[];
    inventory?: BridgeToolEntry[];
    toolInventoryHealth?: string | null;
    toolInventorySource?: string | null;
    toolInventoryFreshness?: string | null;
    toolInventoryTimingsMs?: Record<string, number> | null;
    operatorReadAvailable?: boolean | null;
    cacheHit?: boolean | null;
    backgroundRefresh?: boolean | null;
    inventoryError?: string | null;
    inventoryStale?: boolean | null;
    prefilterTimedOut?: boolean | null;
    prefilterCacheHit?: boolean | null;
    toolInventoryErrors?: {
        stateCatalogError?: string | null;
        cliCatalogError?: string | null;
        sourceScanCatalogError?: string | null;
        gatewayCatalogError?: string | null;
    } | null;
};
type BridgeDoctorReport = {
    summary?: {
        status?: string | null;
        criticalCount?: number | null;
        warningCount?: number | null;
        okCount?: number | null;
        title?: string | null;
        description?: string | null;
        checkedAt?: string | null;
    } | null;
    checks?: Array<{
        key?: string | null;
        status?: string | null;
        title?: string | null;
        description?: string | null;
        details?: string | null;
        data?: Record<string, unknown> | null;
    }> | null;
    repairPlan?: Array<{
        key?: string | null;
        title?: string | null;
        description?: string | null;
        commandHint?: string | null;
    }> | null;
    repairApplied?: Array<{
        key?: string | null;
        title?: string | null;
        description?: string | null;
        error?: string | null;
    }> | null;
    restartRequired?: boolean | null;
    postRepairVerification?: {
        summary?: {
            status?: string | null;
            title?: string | null;
            description?: string | null;
        } | null;
        checks?: Array<{
            key?: string | null;
            status?: string | null;
            title?: string | null;
            description?: string | null;
            details?: string | null;
        }> | null;
    } | null;
};
const DEFAULT_CONFIG: RuntimeConfig = {
    enabled: true,
    scanOnStartup: true,
    hostMode: "managed_local",
    allowedFamilies: ["channel", "plugin"],
    managedLocal: { rootDir: "~/.openclaw", toolingRoot: "", launcherPath: "", autoStart: false },
    externalHost: { baseUrl: "", gatewayBaseUrl: "", authToken: "" },
};
function messageFrom(error: unknown, fallback: string) {
    return error instanceof Error && error.message.trim() ? error.message.trim() : fallback;
}
async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, init);
    const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (!response.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : typeof data.error === "string" ? data.error : `Request failed (${response.status})`);
    }
    return data as T;
}
function humanizeFallbackLabel(value: string, fallback: string) {
    const source = value.trim();
    if (!source) {
        return fallback;
    }
    const normalized = source.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    if (!normalized) {
        return fallback;
    }
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}
function mappedStatusLabel(
    value: string | null | undefined,
    map: Record<string, string>,
    fallback = "Unknown",
) {
    const normalized = String(value || "").trim().toLowerCase();
    return map[normalized] || humanizeFallbackLabel(normalized, fallback);
}
function gatewayLabel(status?: string | null): string {
    const map: Record<string, string> = {
        running: "components.plugin.host.PluginHostWorkbench.k69570f96",
        stopped: "components.plugin.host.PluginHostWorkbench.ka5a2a276",
        cold_stopped: "components.plugin.host.PluginHostWorkbench.k381a7a9a",
        config_invalid: "components.plugin.host.PluginHostWorkbench.kcd377134",
        missing_cli: "components.plugin.host.PluginHostWorkbench.k94fb74b3",
        unreachable: "components.plugin.host.PluginHostWorkbench.k06750c61",
    };
    return mappedStatusLabel(status, map);
}
function ownershipLabel(value?: string | null): string {
    const map: Record<string, string> = {
        v8_owned: "components.plugin.host.PluginHostWorkbench.k1a5e9d16",
        delegated: "components.plugin.host.PluginHostWorkbench.ka5b48143",
        unverified: "components.plugin.host.PluginHostWorkbench.ke7ba1e89",
        disabled: "components.plugin.host.PluginHostWorkbench.kf6be1dc1",
    };
    return mappedStatusLabel(value, map);
}
function supportLabel(value?: string | null): string {
    const map: Record<string, string> = {
        "transport-hosted": "components.plugin.host.PluginHostWorkbench.kdb023059",
        "tool-bridged": "components.plugin.host.PluginHostWorkbench.kfebbc7df",
        "registered only": "components.plugin.host.PluginHostWorkbench.kd4b0f1c2",
        "handoff unsupported": "components.plugin.host.PluginHostWorkbench.kbca28c2e",
    };
    return mappedStatusLabel(value, map, "Unset");
}
function cliSourceLabel(value?: string | null): string {
    const map: Record<string, string> = {
        configured_local: "components.plugin.host.PluginHostWorkbench.kf9e2a177",
        state_root_local: "components.plugin.host.PluginHostWorkbench.k31f673b0",
        global_npm: "components.plugin.host.PluginHostWorkbench.k0e98df40",
        system_path: "components.plugin.host.PluginHostWorkbench.kebeb7d47",
        bundled_local: "components.plugin.host.PluginHostWorkbench.ke4833a10",
        missing: "components.plugin.host.PluginHostWorkbench.k94fb74b3",
    };
    return mappedStatusLabel(value, map);
}
function toolingModeLabel(value?: string | null): string {
    const map: Record<string, string> = {
        prefix_install: "components.plugin.host.PluginHostWorkbench.kacd96362",
        global_install: "components.plugin.host.PluginHostWorkbench.kc3070133",
        system_path: "components.plugin.host.PluginHostWorkbench.kbdf0cb82",
        configured_local: "components.plugin.host.PluginHostWorkbench.k1c96b3b9",
        legacy_bundled: "components.plugin.host.PluginHostWorkbench.k16e24138",
        source_checkout: "components.plugin.host.PluginHostWorkbench.kb72ab57d",
        external_host: "components.plugin.host.PluginHostWorkbench.k0ff5b852",
        missing: "components.plugin.host.PluginHostWorkbench.k8d99f9ee",
    };
    return mappedStatusLabel(value, map);
}
function launcherSourceLabel(value?: string | null): string {
    const map: Record<string, string> = {
        gateway_cmd: "components.plugin.host.PluginHostWorkbench.k19c18f46",
        configured_launcher: "components.plugin.host.PluginHostWorkbench.k66d3e35b",
        direct_cli_run: "components.plugin.host.PluginHostWorkbench.ka480d87e",
    };
    return mappedStatusLabel(value, map);
}
function bridgeProvenanceLabel(value?: string | null): string {
    const map: Record<string, string> = {
        install_record: "components.plugin.host.PluginHostWorkbench.k49abe05a",
        load_path: "components.plugin.host.PluginHostWorkbench.ka6aae691",
        global_auto_discovery: "components.plugin.host.PluginHostWorkbench.kade347f9",
        global_extensions_root: "components.plugin.host.PluginHostWorkbench.bridgeProvenance.globalExtensionsRoot",
        missing: "components.plugin.host.PluginHostWorkbench.k451c35af",
        unknown: "components.plugin.host.PluginHostWorkbench.k8d99f9ee",
    };
    return mappedStatusLabel(value, map);
}
function configSourceLabel(value?: string | null): string {
    const map: Record<string, string> = {
        plugin_entry: "components.plugin.host.PluginHostWorkbench.k03a321fe",
        env: "components.plugin.host.PluginHostWorkbench.k86bc947d",
        defaults: "components.plugin.host.PluginHostWorkbench.kf23992ab",
    };
    return mappedStatusLabel(value, map);
}
function toolInventorySourceLabel(value?: string | null): string {
    const map: Record<string, string> = {
        gateway_rpc: "components.plugin.host.PluginHostWorkbench.kba32cd10",
        durable_cache: "components.plugin.host.PluginHostWorkbench.kef8f70cc",
        plugin_source_scan: "components.plugin.host.PluginHostWorkbench.kcf4f0dec",
        state_manifest: "components.plugin.host.PluginHostWorkbench.k0f035a2e",
        openclaw_log_registered_tools: "components.plugin.host.PluginHostWorkbench.kd08a4764",
    };
    return mappedStatusLabel(value, map);
}
function toolInventoryHealthLabel(value?: string | null): string {
    const map: Record<string, string> = {
        healthy: "components.plugin.host.PluginHostWorkbench.k883b1ef7",
        degraded: "components.plugin.host.PluginHostWorkbench.k40145a50",
    };
    return mappedStatusLabel(value, map);
}
function doctorStatusLabel(value?: string | null): string {
    const map: Record<string, string> = {
        ok: "components.plugin.host.PluginHostWorkbench.kb3f25be4",
        warning: "components.plugin.host.PluginHostWorkbench.k0c47dbdd",
        critical: "components.plugin.host.PluginHostWorkbench.kdf6081a3",
    };
    return mappedStatusLabel(value, map);
}
export function PluginHostWorkbench() {
    const { toast } = useToast();
    const t = useT();
    const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
    const [config, setConfig] = useState<RuntimeConfig>(DEFAULT_CONFIG);
    const [meta, setMeta] = useState<Pick<ConfigRegistryEnvelope, "source" | "savePath" | "reloadRequired"> | null>(null);
    const [extensionsMeta, setExtensionsMeta] = useState<Pick<ConfigRegistryEnvelope, "source" | "savePath" | "reloadRequired"> | null>(null);
    const [extensionsConfig, setExtensionsConfig] = useState<ExtensionsConfigData>({ prefilterPolicy: { enabled: false, mode: "llm_tree" }, modelBindings: { prefilterModel: "" } });
    const [prefilterModels, setPrefilterModels] = useState<SysModel[]>([]);
    const [toolCatalog, setToolCatalog] = useState<BridgeToolCatalog | null>(null);
    const [toolQuery, setToolQuery] = useState("");
    const [previewedToolQuery, setPreviewedToolQuery] = useState("");
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [doctorBusy, setDoctorBusy] = useState<"check" | "repair" | null>(null);
    const [toolConfigBusy, setToolConfigBusy] = useState(false);
    const [toolCatalogBusy, setToolCatalogBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [toolCatalogError, setToolCatalogError] = useState<string | null>(null);
    const hostRefreshInFlightRef = useRef(false);
    const lastHotRefreshAtRef = useRef(0);
    const pollerStateRef = useRef({
        busy: false,
        doctorBusy: null as "check" | "repair" | null,
        toolConfigBusy: false,
        toolCatalogBusy: false,
    });
    useEffect(() => {
        pollerStateRef.current = {
            busy,
            doctorBusy,
            toolConfigBusy,
            toolCatalogBusy,
        };
    }, [busy, doctorBusy, toolConfigBusy, toolCatalogBusy]);
    const loadToolCatalog = useCallback(async (query: string, refresh = false) => {
        setToolCatalogError(null);
        try {
            const nextCatalog = await readJson<{
                selection?: BridgeToolSelection | null;
                exposure?: BridgeToolEntry[];
                inventory?: BridgeToolEntry[];
                toolInventoryHealth?: string | null;
                toolInventorySource?: string | null;
                toolInventoryFreshness?: string | null;
                toolInventoryTimingsMs?: Record<string, number> | null;
                operatorReadAvailable?: boolean | null;
                cacheHit?: boolean | null;
                backgroundRefresh?: boolean | null;
                inventoryError?: string | null;
                inventoryStale?: boolean | null;
                prefilterTimedOut?: boolean | null;
                prefilterCacheHit?: boolean | null;
                toolInventoryErrors?: {
                    stateCatalogError?: string | null;
                    cliCatalogError?: string | null;
                    sourceScanCatalogError?: string | null;
                    gatewayCatalogError?: string | null;
                } | null;
            }>(`/api/plugin-host/bridge/tools?query=${encodeURIComponent(query)}&limit=8${refresh ? "&refresh=true" : ""}`);
            setToolCatalog({
                selection: nextCatalog.selection || null,
                exposure: Array.isArray(nextCatalog.exposure) ? nextCatalog.exposure : [],
                inventory: Array.isArray(nextCatalog.inventory) ? nextCatalog.inventory : [],
                toolInventoryHealth: nextCatalog.toolInventoryHealth || null,
                toolInventorySource: nextCatalog.toolInventorySource || null,
                toolInventoryFreshness: nextCatalog.toolInventoryFreshness || null,
                toolInventoryTimingsMs: nextCatalog.toolInventoryTimingsMs || null,
                operatorReadAvailable: typeof nextCatalog.operatorReadAvailable === "boolean" ? nextCatalog.operatorReadAvailable : null,
                cacheHit: typeof nextCatalog.cacheHit === "boolean" ? nextCatalog.cacheHit : null,
                backgroundRefresh: typeof nextCatalog.backgroundRefresh === "boolean" ? nextCatalog.backgroundRefresh : null,
                inventoryError: typeof nextCatalog.inventoryError === "string" ? nextCatalog.inventoryError : null,
                inventoryStale: typeof nextCatalog.inventoryStale === "boolean" ? nextCatalog.inventoryStale : null,
                prefilterTimedOut: typeof nextCatalog.prefilterTimedOut === "boolean" ? nextCatalog.prefilterTimedOut : null,
                prefilterCacheHit: typeof nextCatalog.prefilterCacheHit === "boolean" ? nextCatalog.prefilterCacheHit : null,
                toolInventoryErrors: nextCatalog.toolInventoryErrors || null,
            });
            return true;
        }
        catch (catalogError) {
            setToolCatalog(null);
            setToolCatalogError(messageFrom(catalogError, t("components.plugin.host.PluginHostWorkbench.ke342b76e")));
            return false;
        }
    }, [t]);
    const refreshHostSnapshot = useCallback(async (refresh = false) => {
        if (hostRefreshInFlightRef.current) {
            return null;
        }
        hostRefreshInFlightRef.current = true;
        try {
            const nextSnapshot = await readJson<Snapshot>(`/api/plugin-host${refresh ? "?refresh=true" : ""}`);
            if (refresh) {
                lastHotRefreshAtRef.current = Date.now();
            }
            setSnapshot(nextSnapshot);
            setError(null);
            return nextSnapshot;
        }
        finally {
            hostRefreshInFlightRef.current = false;
        }
    }, []);
    const load = useCallback(async (quiet = false) => {
        if (!quiet)
            setLoading(true);
        setError(null);
        try {
            const nextSnapshot = await readJson<Snapshot>("/api/plugin-host");
            setSnapshot(nextSnapshot);
            const [domain, extensionDomain, modelList] = await Promise.all([
                fetchConfigDomain<DomainData>("plugin-host").catch(() => null),
                fetchConfigDomain<ExtensionsConfigData>("extensions").catch(() => null),
                fetch("/api/models", { cache: "no-store" })
                    .then((response) => response.json().catch(() => []))
                    .catch(() => []),
            ]);
            setConfig(domain?.data?.config || nextSnapshot.runtimeConfig || DEFAULT_CONFIG);
            setMeta(domain ? { source: domain.source, savePath: domain.savePath, reloadRequired: domain.reloadRequired } : null);
            setExtensionsConfig(extensionDomain?.data || {
                prefilterPolicy: { enabled: false, mode: "llm_tree" },
                modelBindings: { prefilterModel: "" },
            });
            setExtensionsMeta(extensionDomain
                ? { source: extensionDomain.source, savePath: extensionDomain.savePath, reloadRequired: extensionDomain.reloadRequired }
                : null);
            setPrefilterModels(Array.isArray(modelList)
                ? modelList.filter((model: SysModel) => !["EMBEDDING", "RERANK", "RERANKER"].includes(String(model?.type || "").toUpperCase()))
                : []);
        }
        catch (loadError) {
            setError(messageFrom(loadError, t("components.plugin.host.PluginHostWorkbench.k1343d457")));
        }
        finally {
            setLoading(false);
            setBusy(false);
        }
    }, [t]);
    useEffect(() => {
        void load();
    }, [load]);
    useEffect(() => {
        if (loading) {
            return;
        }
        let cancelled = false;
        async function tick() {
            if (cancelled || typeof document !== "undefined" && document.visibilityState !== "visible") {
                return;
            }
            try {
                if (hostRefreshInFlightRef.current) {
                    return;
                }
                const now = Date.now();
                const shouldHotRefresh = now - lastHotRefreshAtRef.current >= 30000;
                await refreshHostSnapshot(shouldHotRefresh);
            }
            catch {
                // 轮询失败不打断当前页面，仍保留手动刷新入口。
            }
        }
        void tick();
        const timer = window.setInterval(() => {
            const pollerState = pollerStateRef.current;
            if (pollerState.busy
                || pollerState.doctorBusy !== null
                || pollerState.toolConfigBusy
                || pollerState.toolCatalogBusy
                || hostRefreshInFlightRef.current) {
                return;
            }
            void tick();
        }, 15000);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [loading, refreshHostSnapshot]);
    async function save() {
        setBusy(true);
        try {
            const domain = await saveConfigDomain<DomainData>("plugin-host", { data: { config } });
            setConfig(domain.data?.config || config);
            setSnapshot(domain.data?.snapshot || snapshot);
            setMeta({ source: domain.source, savePath: domain.savePath, reloadRequired: domain.reloadRequired });
            toast({ title: t("components.plugin.host.PluginHostWorkbench.k474efc3e"), description: t("components.plugin.host.PluginHostWorkbench.kfb008ee7") });
            await load(true);
        }
        catch (saveError) {
            setBusy(false);
            toast({ title: t("components.plugin.host.PluginHostWorkbench.k12769ce1"), description: messageFrom(saveError, t("components.plugin.host.PluginHostWorkbench.k5da4c5ff")), variant: "destructive" });
        }
    }
    async function saveToolSelection() {
        setToolConfigBusy(true);
        try {
            const next = await saveConfigDomain<ExtensionsConfigData>("extensions", {
                data: {
                    prefilterPolicy: {
                        enabled: Boolean(extensionsConfig.prefilterPolicy?.enabled),
                        mode: "llm_tree",
                    },
                    modelBindings: { prefilterModel: String(extensionsConfig.modelBindings?.prefilterModel || "").trim() },
                },
            });
            setExtensionsConfig(next.data || extensionsConfig);
            setExtensionsMeta({ source: next.source, savePath: next.savePath, reloadRequired: next.reloadRequired });
            toast({
                title: t("components.plugin.host.PluginHostWorkbench.k416b25f4"),
                description: t("components.plugin.host.PluginHostWorkbench.k32eaad36"),
            });
            await loadToolCatalog(previewedToolQuery, true);
        }
        catch (saveError) {
            toast({
                title: t("components.plugin.host.PluginHostWorkbench.k12769ce1"),
                description: messageFrom(saveError, t("components.plugin.host.PluginHostWorkbench.k5af6b7d0")),
                variant: "destructive",
            });
        }
        finally {
            setToolConfigBusy(false);
        }
    }
    async function refresh() {
        setBusy(true);
        try {
            await refreshHostSnapshot(true);
        }
        finally {
            setBusy(false);
        }
    }
    async function rescan() {
        setBusy(true);
        try {
            await readJson("/api/plugin-host/rescan", { method: "POST" });
            await load(true);
            toast({ title: t("components.plugin.host.PluginHostWorkbench.k4c38f2a1"), description: t("components.plugin.host.PluginHostWorkbench.k03f791b7") });
        }
        catch (scanError) {
            setBusy(false);
            toast({ title: t("components.plugin.host.PluginHostWorkbench.k9f18fd93"), description: messageFrom(scanError, t("components.plugin.host.PluginHostWorkbench.k86de26ea")), variant: "destructive" });
        }
    }
    async function runDoctor(mode: "check" | "repair") {
        setDoctorBusy(mode);
        try {
            const response = await readJson<{
                status?: string;
                doctor?: BridgeDoctorReport | null;
                pluginHost?: Snapshot | null;
            }>(mode === "repair" ? "/api/plugin-host/doctor/repair" : "/api/plugin-host/doctor?refresh=true", {
                method: mode === "repair" ? "POST" : "GET",
            });
            if (response.pluginHost) {
                setSnapshot(response.pluginHost);
            }
            else {
                await load(true);
            }
            const doctor = response.doctor || null;
            if (mode === "repair") {
                const verification = doctor?.postRepairVerification?.summary;
                toast({
                    title: t("components.plugin.host.PluginHostWorkbench.k61fe36dc"),
                    description: verification?.description
                        || doctor?.summary?.description
                        || t("components.plugin.host.PluginHostWorkbench.k2543c9f3"),
                    variant: String(verification?.status || doctor?.summary?.status || "").trim().toLowerCase() === "critical" ? "destructive" : "default",
                });
            }
            else {
                toast({
                    title: t("components.plugin.host.PluginHostWorkbench.k425b3ce7"),
                    description: doctor?.summary?.description || t("components.plugin.host.PluginHostWorkbench.keb7d86b9"),
                });
            }
        }
        catch (doctorError) {
            toast({
                title: t(mode === "repair" ? "components.plugin.host.PluginHostWorkbench.k18952b83" : "components.plugin.host.PluginHostWorkbench.kfa2bd08d"),
                description: messageFrom(doctorError, t(mode === "repair" ? "components.plugin.host.PluginHostWorkbench.k16b8fb7a" : "components.plugin.host.PluginHostWorkbench.k0091930b")),
                variant: "destructive",
            });
        }
        finally {
            setDoctorBusy(null);
        }
    }
    async function refreshToolInventory() {
        setToolCatalogBusy(true);
        try {
            setPreviewedToolQuery("");
            await loadToolCatalog("", true);
        }
        finally {
            setToolCatalogBusy(false);
        }
    }
    async function previewToolSelection() {
        const query = toolQuery.trim();
        if (!query) {
            toast({
                title: t("components.plugin.host.PluginHostWorkbench.k481e3708"),
                description: t("components.plugin.host.PluginHostWorkbench.k86676b0d"),
            });
            return;
        }
        setToolCatalogBusy(true);
        try {
            const ok = await loadToolCatalog(query, true);
            if (ok) {
                setPreviewedToolQuery(query);
            }
        }
        finally {
            setToolCatalogBusy(false);
        }
    }
    const host = snapshot?.hostSurface;
    const doctorSummary = host?.bridgeDoctorSummary || null;
    const doctorChecks = host?.bridgeDoctorChecks || [];
    const control = snapshot?.controlSurface;
    const proof = host?.recentInboundProof;
    const bridgeProvenance = String(host?.installProvenance || "unknown").trim().toLowerCase();
    const runtimeConfig = snapshot?.runtimeConfig || config;
    const startupState = String(snapshot?.startupState || "cold").trim().toLowerCase();
    const snapshotFreshness = String(snapshot?.snapshotFreshness || "cached").trim().toLowerCase();
    const prefilterEnabled = Boolean(extensionsConfig.prefilterPolicy?.enabled);
    const prefilterModel = String(extensionsConfig.modelBindings?.prefilterModel || "").trim();
    const toolSelection = toolCatalog?.selection || null;
    const previewExposure = previewedToolQuery ? (toolCatalog?.exposure || []) : [];
    const selectionMode = String(toolSelection?.mode || "lexical").trim().toLowerCase();
    const selectionModeLabel = useMemo(() => {
        if (selectionMode === "llm_tree")
            return "components.plugin.host.PluginHostWorkbench.kf0447290";
        if (selectionMode === "fallback")
            return "components.plugin.host.PluginHostWorkbench.ke267f6dc";
        return "components.plugin.host.PluginHostWorkbench.k0dcadb07";
    }, [selectionMode]);
    return (<AdminPageShell>
            <AdminPageHeader title="PluginHostRuntime" description={"components.plugin.host.PluginHostWorkbench.k23b377f7"} badges={["components.plugin.host.PluginHostWorkbench.kf404975f", "components.plugin.host.PluginHostWorkbench.k522ce020"]} actions={<>
                        <Button variant="outline" onClick={() => void runDoctor("check")} disabled={loading || busy || doctorBusy !== null}>
                            {doctorBusy === "check" ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                            {t("components.plugin.host.PluginHostWorkbench.kbaafc2b0")}
                        </Button>
                        <Button variant="outline" onClick={() => void runDoctor("repair")} disabled={loading || busy || doctorBusy !== null}>
                            {doctorBusy === "repair" ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                            {t("components.plugin.host.PluginHostWorkbench.k781fc396")}
                        </Button>
                        <Button variant="outline" onClick={refresh} disabled={loading || busy}>
                            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <RefreshCw className="mr-2 h-4 w-4"/>}
                            {t("components.plugin.host.PluginHostWorkbench.k53d2dd90")}
                        </Button>
                        <Button variant="outline" onClick={rescan} disabled={loading || busy}>
                            {t("components.plugin.host.PluginHostWorkbench.k4d51dc48")}
                        </Button>
                    </>}/>

            <StatusNotice tone="info" title={"components.plugin.host.PluginHostWorkbench.kb01561f0"} description={"components.plugin.host.PluginHostWorkbench.kb08c1cdc"}/>

            {meta ? <SourceMetaRow source={meta.source} savePath={meta.savePath} reloadRequired={meta.reloadRequired}/> : null}
            {error ? <StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.kce9791d2"} description={error}/> : null}
            {!error && startupState === "refreshing" ? (<StatusNotice tone="info" title={"components.plugin.host.PluginHostWorkbench.k2a9afb71"} description={t("components.plugin.host.PluginHostWorkbench.kdb1df7d6", {
            snapshotFreshness_cached: t(snapshotFreshness === "cached" ? "components.plugin.host.PluginHostWorkbench.snapshotFreshnessCached" : "components.plugin.host.PluginHostWorkbench.snapshotFreshnessMinimal"),
            snapshot_lastRefreshAt_snapshot_lastRefreshAt: snapshot?.lastRefreshAt ? t("components.plugin.host.PluginHostWorkbench.lastRefreshAt", { value: snapshot.lastRefreshAt }) : ""
        })}/>) : null}
            {!error && startupState === "error" ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k1a56bae7"} description={snapshot?.lastRefreshError || t("components.plugin.host.PluginHostWorkbench.k06e9ef64")}/>) : null}

            {snapshot?.refreshInFlight || snapshot?.lastLiveRefreshAt || snapshot?.lastDeepRefreshAt ? (<StatusNotice tone={snapshot?.refreshInFlight ? "info" : "success"} title={snapshot?.refreshInFlight ? "components.plugin.host.PluginHostWorkbench.kba8a904a" : "components.plugin.host.PluginHostWorkbench.ka76a6e4e"} description={[
                snapshot?.refreshInFlight ? t("components.plugin.host.PluginHostWorkbench.k91c9fab4") : "",
                snapshot?.lastLiveRefreshAt ? t("components.plugin.host.PluginHostWorkbench.k63c3a54b", {
                    snapshot_lastLiveRefreshAt: snapshot.lastLiveRefreshAt
                }) : "",
                snapshot?.lastDeepRefreshAt ? t("components.plugin.host.PluginHostWorkbench.k02448e15", {
                    snapshot_lastDeepRefreshAt: snapshot.lastDeepRefreshAt
                }) : "",
            ].filter(Boolean).join(" · ")}/>) : null}

            <DomainSummaryStrip items={[
            { label: t("components.plugin.host.PluginHostWorkbench.k12495803"), value: t(startupState === "ready" ? "components.plugin.host.PluginHostWorkbench.k43d7227d" : startupState === "refreshing" ? "components.plugin.host.PluginHostWorkbench.kf16f4ecc" : startupState === "error" ? "components.plugin.host.PluginHostWorkbench.ka4e4dd7c" : "components.plugin.host.PluginHostWorkbench.keba39f5b"), description: [t(snapshotFreshness === "live" ? "components.plugin.host.PluginHostWorkbench.k6a83b15f" : "components.plugin.host.PluginHostWorkbench.k04133485"), snapshot?.refreshInFlight ? t("components.plugin.host.PluginHostWorkbench.k33b363d0") : "", snapshot?.lastLiveRefreshAt ? t("components.plugin.host.PluginHostWorkbench.k63c3a54b", {
                        snapshot_lastLiveRefreshAt: snapshot.lastLiveRefreshAt
                    }) : ""].filter(Boolean).join(" ") },
            { label: t("components.plugin.host.PluginHostWorkbench.k56dd391f"), value: t(runtimeConfig.hostMode === "external" ? "components.plugin.host.PluginHostWorkbench.k0ff5b852" : "components.plugin.host.PluginHostWorkbench.kf2c0cc3a"), description: t(runtimeConfig.hostMode === "external" ? "components.plugin.host.PluginHostWorkbench.k40aeab2e" : "components.plugin.host.PluginHostWorkbench.k25d2465a") },
            { label: "Gateway", value: t(gatewayLabel(host?.gatewayHealth?.runtime?.status)), description: host?.gatewayHealth?.runtime?.detail || t("components.plugin.host.PluginHostWorkbench.k209a6b0a") },
            { label: "RPC", value: t(host?.gatewayHealth?.rpc?.ok ? "components.plugin.host.PluginHostWorkbench.ka6e78d17" : "components.plugin.host.PluginHostWorkbench.k1a83bbab"), description: host?.gatewayHealth?.rpc?.error || t("components.plugin.host.PluginHostWorkbench.k6cec2388") },
            { label: t("components.plugin.host.PluginHostWorkbench.k0e835580"), value: t(ownershipLabel(host?.inboundOwnership)), description: proof?.reason || t("components.plugin.host.PluginHostWorkbench.k572f2a6e") },
        ]}/>

            <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,0.84fr)_minmax(0,1.16fr)]">
                <div className="grid content-start gap-6">
                    <Card className="self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t("components.plugin.host.PluginHostWorkbench.k80585d4a")}</CardTitle>
                            <CardDescription>{t("components.plugin.host.PluginHostWorkbench.k12860ccf")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k7feca8fc")}</div>
                                        <div className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.k3d2b235a")}</div>
                                    </div>
                                    <Switch checked={config.enabled} onCheckedChange={(checked) => setConfig((current) => ({ ...current, enabled: checked }))}/>
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kddfeca27")}</div>
                                        <div className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.kd4541260")}</div>
                                    </div>
                                    <Switch checked={config.scanOnStartup} onCheckedChange={(checked) => setConfig((current) => ({ ...current, scanOnStartup: checked }))}/>
                                </div>
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="plugin-host-mode">{t("components.plugin.host.PluginHostWorkbench.k7657cfbc")}</Label>
                            <select id="plugin-host-mode" value={config.hostMode} onChange={(event) => setConfig((current) => ({ ...current, hostMode: event.target.value === "external" ? "external" : "managed_local" }))} className="flex h-11 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-200">
                                <option value="managed_local">{t("components.plugin.host.PluginHostWorkbench.k99c0f8d2")}</option>
                                <option value="external">{t("components.plugin.host.PluginHostWorkbench.kfd93c189")}</option>
                            </select>
                        </div>

                        {config.hostMode === "managed_local" ? (<>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-root">{t("components.plugin.host.PluginHostWorkbench.k3419fbbe")}</Label>
                                    <Input id="openclaw-root" value={config.managedLocal.rootDir} onChange={(event) => setConfig((current) => ({ ...current, managedLocal: { ...current.managedLocal, rootDir: event.target.value } }))}/>
                                    <p className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.kcdcee916")}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-tooling">{t("components.plugin.host.PluginHostWorkbench.kbad76658")}</Label>
                                    <Input id="openclaw-tooling" value={config.managedLocal.toolingRoot} onChange={(event) => setConfig((current) => ({ ...current, managedLocal: { ...current.managedLocal, toolingRoot: event.target.value } }))}/>
                                    <p className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.k45601a83")}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-launcher">{t("components.plugin.host.PluginHostWorkbench.k953c13f3")}</Label>
                                    <Input id="openclaw-launcher" value={config.managedLocal.launcherPath} onChange={(event) => setConfig((current) => ({ ...current, managedLocal: { ...current.managedLocal, launcherPath: event.target.value } }))}/>
                                    <p className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.kd5378d21")}</p>
                                </div>
                            </>) : (<>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-base-url">{t("components.plugin.host.PluginHostWorkbench.ke766860a")}</Label>
                                    <Input id="openclaw-base-url" value={config.externalHost.baseUrl} onChange={(event) => setConfig((current) => ({ ...current, externalHost: { ...current.externalHost, baseUrl: event.target.value } }))}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-gateway-url">{t("components.plugin.host.PluginHostWorkbench.k8e43301c")}</Label>
                                    <Input id="openclaw-gateway-url" value={config.externalHost.gatewayBaseUrl} onChange={(event) => setConfig((current) => ({ ...current, externalHost: { ...current.externalHost, gatewayBaseUrl: event.target.value } }))}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-auth-token">{t("components.plugin.host.PluginHostWorkbench.kd1ae1455")}</Label>
                                    <Input id="openclaw-auth-token" value={config.externalHost.authToken} onChange={(event) => setConfig((current) => ({ ...current, externalHost: { ...current.externalHost, authToken: event.target.value } }))}/>
                                </div>
                            </>)}

                        <Button onClick={save} disabled={busy}>
                            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                            {t("components.plugin.host.PluginHostWorkbench.k5200d9f5")}
                        </Button>
                    </CardContent>
                    </Card>

                    <Card className="self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t("components.plugin.host.PluginHostWorkbench.k54692380")}</CardTitle>
                            <CardDescription>{t("components.plugin.host.PluginHostWorkbench.kd95a7cb5")}</CardDescription>
                        </CardHeader>
                        <CardContent className="max-h-[460px] space-y-4 overflow-y-auto pr-1">
                            {(snapshot?.plugins || []).length ? ((snapshot?.plugins || []).map((plugin) => (<div key={plugin.pluginId} className="rounded-2xl border border-slate-200 bg-white p-4">
                                        <div className="flex flex-wrap items-start justify-between gap-3">
                                            <div className="space-y-2">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <div className="text-base font-medium text-slate-900">{plugin.displayName || plugin.pluginId}</div>
                                                    <Badge variant="outline" className="border-slate-200 bg-white text-slate-600">{plugin.pluginId}</Badge>
                                                    {plugin.lifecycleState ? <Badge variant="outline">{plugin.lifecycleState}</Badge> : null}
                                                    {plugin.healthState ? <Badge variant="outline">{plugin.healthState}</Badge> : null}
                                                </div>
                                                <div className="grid gap-1 text-xs leading-5 text-slate-600">
                                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.ke37db8cc")}</span>{t(supportLabel(plugin.supportTier))}</div>
                                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k2565d329")}</span>{plugin.activationState || "unknown"} / {plugin.setupState || "unknown"}</div>
                                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kda65094e")}</span>{(plugin.transportCapabilities?.chatTypes || []).join(" / ") || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k0fc34941")}</span>{plugin.transportCapabilities?.groupSupported ? t("components.plugin.host.PluginHostWorkbench.k2ae24b34") : t("components.plugin.host.PluginHostWorkbench.k8d9f05ae")}</div>
                                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k3aa17609")}</span>{plugin.transportCapabilities?.onboardingType || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                                                    {plugin.pluginType === "channel" ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k65183b2d")}</span>{(plugin.channelSurface?.evidence || []).join(" / ") || t("components.plugin.host.PluginHostWorkbench.k899e7b99")}</div>) : null}
                                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kfa511559")}</span>{plugin.familyAdapterReady ? t("components.plugin.host.PluginHostWorkbench.k43d7227d") : t("components.plugin.host.PluginHostWorkbench.k1a83bbab")}</div>
                                                </div>
                                            </div>
                                            <Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
                                                {plugin.onboardingCompleted
                ? t("components.plugin.host.PluginHostWorkbench.k637b4ce7")
                : plugin.supportTier === "transport-hosted" && String(host?.inboundOwnership || "").trim().toLowerCase() === "v8_owned"
                    ? t("components.plugin.host.PluginHostWorkbench.k1a5e9d16")
                    : plugin.supportTier === "tool-bridged"
                        ? t("components.plugin.host.PluginHostWorkbench.k5628c6cc")
                        : t("components.plugin.host.PluginHostWorkbench.k844bf4da")}
                                            </Badge>
                                        </div>
                                        {plugin.unavailableReasons?.length ? (<div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                                                {plugin.unavailableReasons.slice(0, 2).map((reason) => <div key={reason}>{reason}</div>)}
                                            </div>) : null}
                                    </div>))) : (<div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-8 text-sm text-slate-500">
                                    {t("components.plugin.host.PluginHostWorkbench.k6ddc554f")}
                                </div>)}
                        </CardContent>
                    </Card>

                    <Card className="self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t("components.plugin.host.PluginHostWorkbench.k5e31705f")}</CardTitle>
                            <CardDescription>{t("components.plugin.host.PluginHostWorkbench.k6b06242d")}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap gap-3">
                                {control?.dashboardUrl ? (<Button asChild variant="outline" size="sm">
                                        <a href={control.dashboardUrl} target="_blank" rel="noreferrer">
                                            {t("components.plugin.host.PluginHostWorkbench.k0664b93f")}
                                            <ExternalLink className="ml-2 h-4 w-4"/>
                                        </a>
                                    </Button>) : null}
                                {control?.configUrl ? (<Button asChild variant="outline" size="sm">
                                        <a href={control.configUrl} target="_blank" rel="noreferrer">
                                            {t("components.plugin.host.PluginHostWorkbench.k73018841")}
                                            <ExternalLink className="ml-2 h-4 w-4"/>
                                        </a>
                                    </Button>) : null}
                                {control?.docsUrl ? (<Button asChild variant="outline" size="sm">
                                        <a href={control.docsUrl} target="_blank" rel="noreferrer">
                                            {t("components.plugin.host.PluginHostWorkbench.ked868f42")}
                                            <ExternalLink className="ml-2 h-4 w-4"/>
                                        </a>
                                    </Button>) : null}
                            </div>
                            <div className="space-y-2 text-xs leading-5 text-slate-500">
                                <div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k81775b0b")}</span>{control?.dashboardUrl || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">Config：</span>{control?.configUrl || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k5bbf7947")}</span>{control?.docsUrl || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                            </div>
                            {!host?.gatewayHealth?.rpc?.ok ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k00da8763"} description={"components.plugin.host.PluginHostWorkbench.kd4cf3228"}/>) : null}
                        </CardContent>
                    </Card>
                </div>

                <div className="grid content-start gap-6">
                    <Card className="order-2 self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t("components.plugin.host.PluginHostWorkbench.k53610667")}</CardTitle>
                            <CardDescription>{t("components.plugin.host.PluginHostWorkbench.k4c522d1d")}</CardDescription>
                        </CardHeader>
                        <CardContent className="max-h-[860px] space-y-5 overflow-y-auto pr-1">
                            {extensionsMeta ? <SourceMetaRow source={extensionsMeta.source} savePath={extensionsMeta.savePath} reloadRequired={extensionsMeta.reloadRequired}/> : null}
                            <div className="grid gap-4 md:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                                <div className="space-y-4">
                                    <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                        <div className="flex items-center justify-between gap-4">
                                            <div className="space-y-1">
                                                <div className="text-sm font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kfd8be435")}</div>
                                                <div className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.k7ee2e644")}</div>
                                            </div>
                                            <Switch checked={prefilterEnabled} onCheckedChange={(checked) => setExtensionsConfig((current) => ({
            ...current,
            prefilterPolicy: { ...(current.prefilterPolicy || {}), enabled: checked, mode: "llm_tree" },
        }))}/>
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <Label htmlFor="plugin-host-prefilter">{t("components.plugin.host.PluginHostWorkbench.kdf4fce29")}</Label>
                                        <ModelSelect
                                            models={prefilterModels}
                                            value={prefilterModel || "__empty__"}
                                            emptyLabel={t("components.plugin.host.PluginHostWorkbench.kccd8e176")}
                                            placeholder={t("components.plugin.host.PluginHostWorkbench.kccd8e176")}
                                            onValueChange={(value: string) => setExtensionsConfig((current) => ({
                                                ...current,
                                                modelBindings: {
                                                    ...(current.modelBindings || {}),
                                                    prefilterModel: value,
                                                },
                                            }))}
                                        />
                                        <p className="text-xs leading-5 text-slate-500">{t("components.plugin.host.PluginHostWorkbench.k2cebabf6")}</p>
                                    </div>

                                    <div className="flex flex-wrap gap-3">
                                        <Button onClick={saveToolSelection} disabled={toolConfigBusy}>
                                            {toolConfigBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                            {t("components.plugin.host.PluginHostWorkbench.k9148258e")}
                                        </Button>
                                        <Button variant="outline" onClick={refreshToolInventory} disabled={toolCatalogBusy}>
                                            {toolCatalogBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <RefreshCw className="mr-2 h-4 w-4"/>}
                                            {t("components.plugin.host.PluginHostWorkbench.kcbad0e83")}
                                        </Button>
                                    </div>
                                </div>

                                <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k1e41108d")}</span>
                                        <Badge variant={selectionMode === "llm_tree" ? "default" : selectionMode === "fallback" ? "secondary" : "outline"}>{t(selectionModeLabel)}</Badge>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span>{t("components.plugin.host.PluginHostWorkbench.k5252b77a")}</span>
                                        <Badge variant="outline">{toolSelection?.role || "extensions_prefilter"}</Badge>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span>{t("components.plugin.host.PluginHostWorkbench.kca695f8f")}</span>
                                        <Badge variant="outline" className="max-w-[220px] truncate">{toolSelection?.modelId || prefilterModel || t("components.plugin.host.PluginHostWorkbench.k54745147")}</Badge>
                                    </div>
                                    <div className="grid grid-cols-3 gap-3 text-xs">
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                            <div className="text-slate-500">{t("components.plugin.host.PluginHostWorkbench.kecf965a6")}</div>
                                            <div className="mt-1 font-semibold text-slate-900">{toolSelection?.poolSize ?? 0}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                            <div className="text-slate-500">{t("components.plugin.host.PluginHostWorkbench.kf1a1154a")}</div>
                                            <div className="mt-1 font-semibold text-slate-900">{toolSelection?.callableSize ?? 0}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                            <div className="text-slate-500">{t("components.plugin.host.PluginHostWorkbench.kc6203a22")}</div>
                                            <div className="mt-1 font-semibold text-slate-900">{toolSelection?.inventorySize ?? 0}</div>
                                        </div>
                                    </div>
                                    {toolSelection?.timingsMs ? (<div className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-xs text-slate-500">
                                            bridge state {toolSelection.timingsMs.bridgeStateMs ?? toolSelection.timingsMs.bridgeState ?? 0}ms · engine cache {toolSelection.timingsMs.engineInventoryCacheMs ?? 0}ms · bridge tools {toolSelection.timingsMs.bridgeToolsRequestMs ?? toolSelection.timingsMs.gatewayInventory ?? 0}ms · lexical {toolSelection.timingsMs.lexicalMs ?? toolSelection.timingsMs.lexical ?? 0}ms · selection {toolSelection.timingsMs.selectionMs ?? 0}ms · total {toolSelection.timingsMs.totalMs ?? 0}ms
                                            {typeof toolSelection.timingsMs.prefilterMs === "number"
                ? ` · prefilter ${toolSelection.timingsMs.prefilterMs}ms`
                : typeof toolSelection.timingsMs.prefilter === "number"
                    ? ` · prefilter ${toolSelection.timingsMs.prefilter}ms`
                    : typeof toolSelection.timingsMs.rerank === "number"
                        ? ` · prefilter ${toolSelection.timingsMs.rerank}ms`
                        : ""}
                                        </div>) : null}
                                    {toolSelection?.reason ? (<div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-xs leading-5 text-amber-800">
                                            {toolSelection.reason}
                                        </div>) : null}
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="plugin-host-tool-query">{t("components.plugin.host.PluginHostWorkbench.k68fae3ef")}</Label>
                                <p className="text-xs leading-5 text-slate-500">
                                    {t("components.plugin.host.PluginHostWorkbench.k8232b79a")}
                                </p>
                                <div className="flex gap-3">
                                    <Input id="plugin-host-tool-query" value={toolQuery} onChange={(event) => setToolQuery(event.target.value)} placeholder={t("components.plugin.host.PluginHostWorkbench.k4896918e")}/>
                                    <Button variant="outline" onClick={previewToolSelection} disabled={toolCatalogBusy || !toolQuery.trim()}>
                                        {toolCatalogBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                        {t("components.plugin.host.PluginHostWorkbench.k76932896")}
                                    </Button>
                                </div>
                            </div>

                            {toolCatalogError ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k4967cf19"} description={toolCatalogError}/>) : null}

                            {toolCatalog ? (<div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-700">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k1a1451ae")}</span>
                                        <Badge variant={toolCatalog.toolInventoryHealth === "healthy" ? "default" : "secondary"}>
                                            {t(toolInventoryHealthLabel(toolCatalog.toolInventoryHealth))}
                                        </Badge>
                                        <Badge variant="outline">
                                            {t(toolInventorySourceLabel(toolCatalog.toolInventorySource))}
                                        </Badge>
                                        {toolCatalog.toolInventoryFreshness ? (<Badge variant="outline">{toolCatalog.toolInventoryFreshness}</Badge>) : null}
                                        {toolCatalog.cacheHit === true ? (<Badge variant="outline">{t("components.plugin.host.PluginHostWorkbench.k36715bf2")}</Badge>) : null}
                                        {toolCatalog.backgroundRefresh ? (<Badge variant="secondary">{t("components.plugin.host.PluginHostWorkbench.k3bc09409")}</Badge>) : null}
                                        {toolCatalog.inventoryStale ? (<Badge variant="secondary">{t("components.plugin.host.PluginHostWorkbench.k4b8f07d9")}</Badge>) : null}
                                    </div>
                                    <div className="grid max-h-48 gap-2 overflow-y-auto pr-1 text-xs text-slate-500">
                                        <div>{t("components.plugin.host.PluginHostWorkbench.keb9710fb")}</div>
                                        <div>
                                            <span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kbb9108df")}</span>
                                            {toolCatalog.operatorReadAvailable === true
                ? t("components.plugin.host.PluginHostWorkbench.kbc4b05f8")
                : toolCatalog.operatorReadAvailable === false
                    ? t("components.plugin.host.PluginHostWorkbench.k0deb2ae1")
                    : t("components.plugin.host.PluginHostWorkbench.ke87c84f2")}
                                        </div>
                                        {toolCatalog.toolInventoryErrors?.stateCatalogError ? (<div><span className="font-medium text-slate-900">state manifest：</span>{toolCatalog.toolInventoryErrors.stateCatalogError}</div>) : null}
                                        {toolCatalog.toolInventoryErrors?.cliCatalogError ? (<div><span className="font-medium text-slate-900">CLI：</span>{toolCatalog.toolInventoryErrors.cliCatalogError}</div>) : null}
                                        {toolCatalog.toolInventoryErrors?.sourceScanCatalogError ? (<div><span className="font-medium text-slate-900">source scan：</span>{toolCatalog.toolInventoryErrors.sourceScanCatalogError}</div>) : null}
                                        {toolCatalog.toolInventoryErrors?.gatewayCatalogError ? (<div><span className="font-medium text-slate-900">gateway RPC：</span>{toolCatalog.toolInventoryErrors.gatewayCatalogError}</div>) : null}
                                        {toolCatalog.inventoryError ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.ka227bfb7")}</span>{toolCatalog.inventoryError}</div>) : null}
                                        {toolCatalog.toolInventoryTimingsMs ? (<div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px] text-slate-500">
                                                {`gateway RPC ${toolCatalog.toolInventoryTimingsMs.gatewayRpcMs ?? 0}ms · durable cache ${toolCatalog.toolInventoryTimingsMs.durableCacheMs ?? 0}ms · source scan ${toolCatalog.toolInventoryTimingsMs.sourceScanMs ?? 0}ms · state manifest ${toolCatalog.toolInventoryTimingsMs.stateManifestMs ?? 0}ms · total ${toolCatalog.toolInventoryTimingsMs.totalMs ?? 0}ms`}
                                            </div>) : null}
                                        {toolCatalog.selection?.timingsMs ? (<div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px] text-slate-500">
                                                {`engine bridge-state ${toolCatalog.selection.timingsMs.bridgeStateMs ?? 0}ms · inventory ${toolCatalog.selection.timingsMs.engineInventoryCacheMs ?? 0}ms · selection ${toolCatalog.selection.timingsMs.selectionMs ?? 0}ms · prefilter ${toolCatalog.selection.timingsMs.prefilterMs ?? 0}ms`}
                                            </div>) : null}
                                    </div>
                                </div>) : null}

                            <div className="space-y-3">
                                <div className="flex flex-wrap items-center gap-2">
                                    <div className="text-sm font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k3d03e0e5")}</div>
                                    {previewedToolQuery ? <Badge variant="outline" className="max-w-[360px] truncate">{previewedToolQuery}</Badge> : null}
                                    {toolCatalog?.prefilterTimedOut ? <Badge variant="secondary">{t("components.plugin.host.PluginHostWorkbench.kb949aeaa")}</Badge> : null}
                                    {toolCatalog?.prefilterCacheHit ? <Badge variant="outline">{t("components.plugin.host.PluginHostWorkbench.k27c81250")}</Badge> : null}
                                </div>
                                {previewExposure.length ? (<div className="max-h-80 space-y-3 overflow-y-auto pr-1">
                                        {previewExposure.map((tool) => (<div key={tool.canonicalName} className="rounded-2xl border border-slate-200 bg-white p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <div className="text-sm font-medium text-slate-900">{tool.label || tool.canonicalName}</div>
                                                    <Badge variant="outline">{tool.canonicalName}</Badge>
                                                    <Badge variant="secondary">{tool.pluginId || "gateway"}</Badge>
                                                </div>
                                                <div className="mt-2 text-xs leading-5 text-slate-500">{tool.description || t("components.plugin.host.PluginHostWorkbench.k35746d08")}</div>
                                            </div>))}
                                    </div>) : (<div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                        {previewedToolQuery
                ? t("components.plugin.host.PluginHostWorkbench.k29c5d89a")
                : t("components.plugin.host.PluginHostWorkbench.kfb39ea0e")}
                                    </div>)}
                                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-xs leading-5 text-slate-500">
                                    {t("components.plugin.host.PluginHostWorkbench.k5a80d1a5")}
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="order-1 self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t("components.plugin.host.PluginHostWorkbench.kbb771e48")}</CardTitle>
                            <CardDescription>{t("components.plugin.host.PluginHostWorkbench.k2d338e02")}</CardDescription>
                        </CardHeader>
                        <CardContent className="max-h-[760px] space-y-3 overflow-y-auto pr-1 text-sm text-slate-700">
                            {doctorSummary ? (<div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-medium text-slate-900">{doctorSummary.title || t("components.plugin.host.PluginHostWorkbench.k924b7013")}</span>
                                        <Badge variant={String(doctorSummary.status || "").trim().toLowerCase() === "critical" ? "destructive" : String(doctorSummary.status || "").trim().toLowerCase() === "warning" ? "secondary" : "default"}>
                                            {t(doctorStatusLabel(doctorSummary.status))}
                                        </Badge>
                                        {typeof doctorSummary.criticalCount === "number" ? <Badge variant="outline">{t("components.plugin.host.PluginHostWorkbench.ka71b5356", {
            doctorSummary_criticalCount: doctorSummary.criticalCount
        })}</Badge> : null}
                                        {typeof doctorSummary.warningCount === "number" ? <Badge variant="outline">{t("components.plugin.host.PluginHostWorkbench.k703b52d9", {
            doctorSummary_warningCount: doctorSummary.warningCount
        })}</Badge> : null}
                                        {typeof doctorSummary.okCount === "number" ? <Badge variant="outline">{t("components.plugin.host.PluginHostWorkbench.k06adc853", {
            doctorSummary_okCount: doctorSummary.okCount
        })}</Badge> : null}
                                    </div>
                                    {doctorSummary.description ? (<div className="mt-2 text-xs leading-5 text-slate-500">{doctorSummary.description}</div>) : null}
                                    {doctorSummary.checkedAt ? (<div className="mt-2 text-[11px] text-slate-400">{t("components.plugin.host.PluginHostWorkbench.k9b29b5c5")}{doctorSummary.checkedAt}</div>) : null}
                                </div>) : null}
                            {doctorChecks.length ? (<details className="rounded-2xl border border-slate-200 bg-white p-4">
                                    <summary className="cursor-pointer text-sm font-medium text-slate-900">
                                        {t("components.plugin.host.PluginHostWorkbench.k03dd74f4", {
            doctorChecks_length: doctorChecks.length
        })}
                                    </summary>
                                    <div className="mt-3 max-h-80 space-y-3 overflow-y-auto pr-1">
                                        {doctorChecks.map((check, index) => (<div key={`${check.key || "doctor-check"}-${index}`} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <div className="font-medium text-slate-900">{check.title || check.key || t("components.plugin.host.PluginHostWorkbench.k1fbaf0d1")}</div>
                                                    <Badge variant={String(check.status || "").trim().toLowerCase() === "critical" ? "destructive" : String(check.status || "").trim().toLowerCase() === "warning" ? "secondary" : "default"}>
                                                        {t(doctorStatusLabel(check.status))}
                                                    </Badge>
                                                    {check.key ? <Badge variant="outline">{check.key}</Badge> : null}
                                                </div>
                                                {check.description ? <div className="mt-2 text-xs leading-5 text-slate-500">{check.description}</div> : null}
                                                {check.details ? <div className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap break-all rounded-xl bg-white px-3 py-2 text-xs leading-5 text-slate-600">{check.details}</div> : null}
                                            </div>))}
                                    </div>
                                </details>) : null}
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k5cd2e1e1")}</span>{t(cliSourceLabel(host?.cliSource))}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k3ebe177a")}</span>{t(toolingModeLabel(host?.toolingMode))}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k5a1b86f6")}</span>{host?.toolingEntry || t("components.plugin.host.PluginHostWorkbench.kf6ffaaa1")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k680afcb2")}</span>{host?.launcherMissing
                                ? t("components.plugin.host.PluginHostWorkbench.launcherMissing", {
                                    source: t(launcherSourceLabel(host?.launcherSource)),
                                })
                                : t(launcherSourceLabel(host?.launcherSource))}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k75e1176f")}</span>{host?.lifecycleAuthority || "unknown"}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kc9270402")}</span>{host?.bridgeStatusStale ? t("components.plugin.host.PluginHostWorkbench.k3d570918") : host?.bridgeReady ? `ready (${host?.bridgePluginId || "unknown"})` : "unready"}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k2995d436")}</span>{(host?.managedChannels || []).join(" / ") || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kf393549d")}</span>{t(bridgeProvenanceLabel(host?.installProvenance))}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k18d77f7f")}</span>{host?.refreshMode || "hot"}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k94b7eed4")}</span>{host?.bridgeStatusSource || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k8748ec74")}</span>{typeof host?.bridgeStatusMs === "number" ? `${host.bridgeStatusMs}ms` : t("components.plugin.host.PluginHostWorkbench.k8c58d0c0")}</div>
                            {host?.bridgeStatusObservedAt ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k6287bdf1")}</span>{host.bridgeStatusObservedAt}</div>) : null}
                            {host?.bridgeStatusStale ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k023df888")}</span>{t("components.plugin.host.PluginHostWorkbench.kee1cab41")}</div>) : null}
                            {host?.bridgeStatusError ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kd5c1571d")}</span>{host.bridgeStatusError}</div>) : null}
                            <div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kd8654afd")}</span>{host?.resolvedStateDir || t("components.plugin.host.PluginHostWorkbench.kf6ffaaa1")}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k04d34c78")}</span>{host?.gatewayBaseUrl || t("components.plugin.host.PluginHostWorkbench.kf6ffaaa1")}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k912fc413")}</span>{host?.v8InboundUrl || t("components.plugin.host.PluginHostWorkbench.kf6ffaaa1")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k8a6b24ee")}</span>{host?.pluginsAllowConfigured ? (host?.pluginsAllow || []).join(" / ") || t("components.plugin.host.PluginHostWorkbench.kc3784679") : t("components.plugin.host.PluginHostWorkbench.k538488d4")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kde43515c")}</span>{host?.managedChannelsSource || t("components.plugin.host.PluginHostWorkbench.ka2c3f5c1")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k13fa6851")}</span>{t(configSourceLabel(host?.configSource))}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k5877a18c")}</span>{host?.handoffConfigured ? t("components.plugin.host.PluginHostWorkbench.k5131bc5c") : t("components.plugin.host.PluginHostWorkbench.k7c6ef77d")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kc725dc57")}</span>{host?.claimEnabled ? t("components.plugin.host.PluginHostWorkbench.kdb6c0cc1") : t("components.plugin.host.PluginHostWorkbench.kf77a41cd")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k6f96b875")}</span>{host?.lastClaimAt || t("components.plugin.host.PluginHostWorkbench.k67cce65d")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k399d7f2b")}</span>{host?.lastClaimAttemptAt || t("components.plugin.host.PluginHostWorkbench.k67cce65d")}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kcd523400")}</span>{host?.lastClaimOutcome || t("components.plugin.host.PluginHostWorkbench.k67cce65d")}</div>
                            {host?.lastClaimDeclineReason ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k81df5c07")}</span>{host.lastClaimDeclineReason}</div>) : null}
                            {host?.lastClaimChannel ? (<div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k59573488")}</span>{host.lastClaimChannel}</div>) : null}
                            {host?.lastClaimConversation ? (<div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k74a50ea9")}</span>{host.lastClaimConversation}</div>) : null}
                            {host?.lastClaimMessageId ? (<div className="break-all"><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k72873462")}</span>{host.lastClaimMessageId}</div>) : null}
                            {host?.lastClaimPayloadShape ? (<div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-xs leading-5 text-slate-600">
                                    <div className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.ka49ed9db")}</div>
                                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(host.lastClaimPayloadShape, null, 2)}</pre>
                                </div>) : null}
                            {(host?.fieldContractWarnings || []).map((warning, index) => (<StatusNotice key={`field-contract-warning-${index}`} tone="warning" title={warning.title || "components.plugin.host.PluginHostWorkbench.k69eb5289"} description={`${warning.description || ""}${(warning.fields || []).length ? `\n${t("components.plugin.host.PluginHostWorkbench.ke054e6e1")}${(warning.fields || []).join(", ")}` : ""}`.trim()}/>))}
                            {host?.expectedBridgeClaimMissed ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k34a0b533"} description={"components.plugin.host.PluginHostWorkbench.k79e4d00c"}/>) : null}
                            {(host?.pluginProvenanceWarnings || []).map((warning, index) => (<StatusNotice key={`plugin-provenance-warning-${index}`} tone="warning" title={warning.title || "components.plugin.host.PluginHostWorkbench.kef51f8f0"} description={`${warning.description || ""}${warning.pluginId ? `\nplugin: ${warning.pluginId}` : ""}${(warning.pluginIds || []).length ? `\nplugins: ${(warning.pluginIds || []).join(", ")}` : ""}`.trim()}/>))}
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k82e381cd")}</span>{host?.handoffReady ? "ready" : "unready"}{host?.handoffDrift ? t("components.plugin.host.PluginHostWorkbench.kdc92c921") : ""}</div>
                            <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.kf6e5d49e")}</span>{host?.lastInboundHandoffAt || t("components.plugin.host.PluginHostWorkbench.k67cce65d")}</div>
                            {bridgeProvenance === "global_auto_discovery" ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k7279c68e"} description={"components.plugin.host.PluginHostWorkbench.kbaf4aa67"}/>) : bridgeProvenance === "missing" ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k1e81a230"} description={"components.plugin.host.PluginHostWorkbench.k01e07bc6"}/>) : host?.handoffConfigured === false || host?.lastClaimDeclineReason === "handoff_token_missing" ? (<StatusNotice tone="warning" title={"components.plugin.host.PluginHostWorkbench.k0604b2cc"} description={"components.plugin.host.PluginHostWorkbench.ka8ef5b0d"}/>) : null}
                            {proof ? (<div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-xs leading-5">
                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k20e0f348")}</span>{proof.stage || t("components.plugin.host.PluginHostWorkbench.ka71eba57")}</div>
                                    <div><span className="font-medium text-slate-900">{t("components.plugin.host.PluginHostWorkbench.k4f8e3d24")}</span>{proof.inboundObservedAt || t("components.plugin.host.PluginHostWorkbench.k899e7b99")}</div>
                                    <div><span className="font-medium text-slate-900">run：</span>{proof.runId || t("components.plugin.host.PluginHostWorkbench.k9d95e519")}</div>
                                    <div><span className="font-medium text-slate-900">push：</span>{proof.pushRunId || t("components.plugin.host.PluginHostWorkbench.k9d95e519")}{proof.pushStatus ? ` (${proof.pushStatus})` : ""}</div>
                                    {proof.reason ? <div className="mt-2 text-slate-500">{proof.reason}</div> : null}
                                </div>) : null}
                        </CardContent>
                    </Card>

                </div>
            </div>

        </AdminPageShell>);
}
