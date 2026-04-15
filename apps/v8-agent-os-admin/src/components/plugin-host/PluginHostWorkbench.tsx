"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
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
import { lt, type LocalizedText } from "@/lib/locale";

type HostMode = "managed_local" | "external";

type RuntimeConfig = {
    enabled: boolean;
    scanOnStartup: boolean;
    hostMode: HostMode;
    allowedFamilies: string[];
    managedLocal: { rootDir: string; toolingRoot: string; launcherPath: string; autoStart: boolean };
    externalHost: { baseUrl: string; gatewayBaseUrl: string; authToken: string };
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
    controlSurface?: { dashboardUrl?: string | null; configUrl?: string | null; docsUrl?: string | null };
    summary?: { pluginCount?: number; activeCount?: number; channelPluginCount?: number };
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
            runtime?: { status?: string | null; detail?: string | null };
            rpc?: { ok?: boolean; error?: string | null };
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
        transportCapabilities?: { chatTypes?: string[] | null; groupSupported?: boolean; onboardingType?: string | null } | null;
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

type DomainData = { config: RuntimeConfig; snapshot?: Snapshot };

type SysModel = {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
    providerName?: string;
};

type ExtensionsConfigData = {
    prefilterPolicy?: { enabled?: boolean; mode?: string };
    modelBindings?: { prefilterModel?: string };
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

function modelValue(model: SysModel) {
    return String(model.modelId || model.id || "").trim();
}

function modelLabel(model: SysModel) {
    const providerName = model.provider?.name || model.providerName || "";
    return `${model.name || modelValue(model)}${providerName ? ` (${providerName})` : ""}`;
}

function gatewayLabel(status?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        running: lt("运行中", "Running"),
        stopped: lt("未启动", "Stopped"),
        cold_stopped: lt("已冷停", "Cold stopped"),
        config_invalid: lt("配置无效", "Invalid config"),
        missing_cli: lt("未解析到 CLI", "CLI missing"),
        unreachable: lt("不可达", "Unreachable"),
    };
    return map[String(status || "").trim().toLowerCase()] || String(status || "unknown");
}

function ownershipLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        v8_owned: lt("V8 已接住真实入站", "V8 owns live inbound"),
        delegated: lt("仍由 OpenClaw sidecar 处理", "Handled by OpenClaw sidecar"),
        unverified: lt("等待新的真实入站证明", "Waiting for fresh inbound proof"),
        disabled: lt("当前未接管", "Not managed"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function supportLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        "transport-hosted": lt("渠道传输已托管", "Transport hosted"),
        "tool-bridged": lt("工具桥已接通", "Tool bridge ready"),
        "registered only": lt("仅注册展示", "Registered only"),
        "handoff unsupported": lt("接棒未就绪", "Handoff unsupported"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unset");
}

function cliSourceLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        configured_local: lt("显式指定的本地 CLI", "Configured local CLI"),
        state_root_local: lt("状态目录内 CLI", "CLI inside state root"),
        global_npm: lt("Windows 全局 npm 安装", "Windows global npm install"),
        system_path: lt("系统 PATH", "System PATH"),
        bundled_local: lt("旧 bundled CLI", "Legacy bundled CLI"),
        missing: lt("未解析到 CLI", "CLI missing"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function toolingModeLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        prefix_install: lt("官方前缀安装", "Prefix install"),
        global_install: lt("官方全局安装", "Global install"),
        system_path: lt("系统 PATH CLI", "System PATH CLI"),
        configured_local: lt("显式指定 toolingRoot", "Configured toolingRoot"),
        legacy_bundled: lt("旧 bundled 布局", "Legacy bundled layout"),
        source_checkout: lt("源码检出", "Source checkout"),
        external_host: lt("外部 OpenClaw host", "External host"),
        missing: lt("未识别", "Unknown"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function launcherSourceLabel(value?: string | null, missing?: boolean): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        gateway_cmd: lt("gateway.cmd", "gateway.cmd"),
        configured_launcher: lt("显式指定脚本", "Configured launcher"),
        direct_cli_run: lt("直接 CLI 启动", "Direct CLI run"),
    };
    const base = map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
    return typeof base === "string"
        ? (missing ? `${base} (missing)` : base)
        : missing
            ? lt(`${base["zh-CN"]}（当前未检测到脚本）`, `${base.en} (missing)`)
            : base;
}

function bridgeProvenanceLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        install_record: lt("正式 install 记录", "Install record"),
        load_path: lt("load-path provenance", "Load-path provenance"),
        global_auto_discovery: lt("全局自动发现（未追踪）", "Global auto-discovery (untracked)"),
        missing: lt("未安装 / 未链接", "Missing (not installed or linked)"),
        unknown: lt("未识别", "Unknown"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function configSourceLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        plugin_entry: lt("插件私有配置", "Plugin entry config"),
        env: lt("环境变量注入", "Environment override"),
        defaults: lt("默认值", "Defaults"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function toolInventorySourceLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        gateway_rpc: lt("Gateway RPC 实时目录", "Gateway RPC live catalog"),
        durable_cache: lt("持久化缓存目录", "Durable cached inventory"),
        plugin_source_scan: lt("源码扫描目录", "Plugin source scan"),
        state_manifest: lt("静态 manifest", "Static manifest"),
        openclaw_log_registered_tools: lt("OpenClaw 日志恢复目录", "OpenClaw log recovered inventory"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function toolInventoryHealthLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        healthy: lt("完整", "Healthy"),
        degraded: lt("退化", "Degraded"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
}

function doctorStatusLabel(value?: string | null): LocalizedText | string {
    const map: Record<string, LocalizedText> = {
        ok: lt("通过", "OK"),
        warning: lt("警告", "Warning"),
        critical: lt("阻断", "Critical"),
    };
    return map[String(value || "").trim().toLowerCase()] || String(value || "unknown");
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
        } catch (catalogError) {
            setToolCatalog(null);
            setToolCatalogError(messageFrom(catalogError, t(lt("读取工具目录失败。", "Failed to load bridge tools."))));
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
        } finally {
            hostRefreshInFlightRef.current = false;
        }
    }, []);

    const load = useCallback(async (quiet = false) => {
        if (!quiet) setLoading(true);
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
            setExtensionsConfig(
                extensionDomain?.data || {
                    prefilterPolicy: { enabled: false, mode: "llm_tree" },
                    modelBindings: { prefilterModel: "" },
                },
            );
            setExtensionsMeta(
                extensionDomain
                    ? { source: extensionDomain.source, savePath: extensionDomain.savePath, reloadRequired: extensionDomain.reloadRequired }
                    : null,
            );
            setPrefilterModels(
                Array.isArray(modelList)
                    ? modelList.filter((model: SysModel) => !["EMBEDDING", "RERANK", "RERANKER"].includes(String(model?.type || "").toUpperCase()))
                    : [],
            );
        } catch (loadError) {
            setError(messageFrom(loadError, t(lt("读取 PluginHostRuntime 状态失败。", "Failed to load PluginHostRuntime state."))));
        } finally {
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
            } catch {
                // 轮询失败不打断当前页面，仍保留手动刷新入口。
            }
        }
        void tick();
        const timer = window.setInterval(() => {
            const pollerState = pollerStateRef.current;
            if (
                pollerState.busy
                || pollerState.doctorBusy !== null
                || pollerState.toolConfigBusy
                || pollerState.toolCatalogBusy
                || hostRefreshInFlightRef.current
            ) {
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
            toast({ title: t(lt("连接设置已保存", "Connection saved")), description: t(lt("PluginHostRuntime 会按新的 OpenClaw 连接参数重新解释宿主状态。", "PluginHostRuntime will reinterpret host state with the new OpenClaw connection settings.")) });
            await load(true);
        } catch (saveError) {
            setBusy(false);
            toast({ title: t(lt("保存失败", "Save failed")), description: messageFrom(saveError, t(lt("PluginHostRuntime 配置保存失败。", "Failed to save PluginHostRuntime config."))), variant: "destructive" });
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
                title: t(lt("工具筛选已保存", "Tool selection saved")),
                description: t(lt("PluginHostRuntime 的工具候选会继续写回 extensions 的 canonical source。", "PluginHostRuntime keeps writing tool selection back to the extensions canonical source.")),
            });
            await loadToolCatalog(previewedToolQuery, true);
        } catch (saveError) {
            toast({
                title: t(lt("保存失败", "Save failed")),
                description: messageFrom(saveError, t(lt("工具筛选配置保存失败。", "Failed to save tool selection config."))),
                variant: "destructive",
            });
        } finally {
            setToolConfigBusy(false);
        }
    }

    async function refresh() {
        setBusy(true);
        try {
            await refreshHostSnapshot(true);
        } finally {
            setBusy(false);
        }
    }

    async function rescan() {
        setBusy(true);
        try {
            await readJson("/api/plugin-host/rescan", { method: "POST" });
            await load(true);
            toast({ title: t(lt("已重新扫描", "Rescanned")), description: t(lt("PluginHostRuntime 已重新读取当前 OpenClaw 状态目录。", "PluginHostRuntime reloaded the current OpenClaw state root.")) });
        } catch (scanError) {
            setBusy(false);
            toast({ title: t(lt("重新扫描失败", "Rescan failed")), description: messageFrom(scanError, t(lt("PluginHostRuntime 重新扫描失败。", "PluginHostRuntime rescan failed."))), variant: "destructive" });
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
            } else {
                await load(true);
            }
            const doctor = response.doctor || null;
            if (mode === "repair") {
                const verification = doctor?.postRepairVerification?.summary;
                toast({
                    title: t(lt("修复已执行", "Repair applied")),
                    description: verification?.description
                        || doctor?.summary?.description
                        || t(lt("已执行 plugin_host doctor/repair，请继续查看下方复检结果。", "plugin_host doctor/repair has been applied. Review the post-repair verification below.")),
                    variant: String(verification?.status || doctor?.summary?.status || "").trim().toLowerCase() === "critical" ? "destructive" : "default",
                });
            } else {
                toast({
                    title: t(lt("检查完成", "Doctor completed")),
                    description: doctor?.summary?.description || t(lt("已刷新 bridge doctor 结果。", "Bridge doctor results refreshed.")),
                });
            }
        } catch (doctorError) {
            toast({
                title: t(mode === "repair" ? lt("修复失败", "Repair failed") : lt("检查失败", "Doctor failed")),
                description: messageFrom(doctorError, t(mode === "repair" ? lt("plugin_host doctor/repair 执行失败。", "plugin_host doctor/repair failed.") : lt("plugin_host doctor 执行失败。", "plugin_host doctor failed."))),
                variant: "destructive",
            });
        } finally {
            setDoctorBusy(null);
        }
    }

    async function refreshToolInventory() {
        setToolCatalogBusy(true);
        try {
            setPreviewedToolQuery("");
            await loadToolCatalog("", true);
        } finally {
            setToolCatalogBusy(false);
        }
    }

    async function previewToolSelection() {
        const query = toolQuery.trim();
        if (!query) {
            toast({
                title: t(lt("请输入模拟查询", "Enter a preview query")),
                description: t(lt("筛选预览只用于诊断。请输入一条模拟用户需求后再预览候选。", "Selection preview is diagnostic only. Enter a simulated user request before previewing candidates.")),
            });
            return;
        }
        setToolCatalogBusy(true);
        try {
            const ok = await loadToolCatalog(query, true);
            if (ok) {
                setPreviewedToolQuery(query);
            }
        } finally {
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
        if (selectionMode === "llm_tree") return lt("LLM 工具树预筛", "LLM tree prefilter");
        if (selectionMode === "fallback") return lt("回退到 lexical", "Fallback");
        return lt("Lexical 预筛选", "Lexical");
    }, [selectionMode]);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="PluginHostRuntime"
                description={lt("V8 负责连接真实 OpenClaw、观测宿主健康和 handoff 状态。插件安装、配置、wizard 与 pairing 默认回到 OpenClaw 控制台完成。", "V8 connects to the live OpenClaw host, observes health, and tracks handoff state. Plugin install, setup, wizard, and pairing stay in the OpenClaw console.")}
                badges={[lt("薄桥接页", "Thin bridge"), lt("官方安装版优先", "Official install first")]}
                actions={
                    <>
                        <Button variant="outline" onClick={() => void runDoctor("check")} disabled={loading || busy || doctorBusy !== null}>
                            {doctorBusy === "check" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("检查", "Doctor"))}
                        </Button>
                        <Button variant="outline" onClick={() => void runDoctor("repair")} disabled={loading || busy || doctorBusy !== null}>
                            {doctorBusy === "repair" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("检查并修复", "Doctor & repair"))}
                        </Button>
                        <Button variant="outline" onClick={refresh} disabled={loading || busy}>
                            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            {t(lt("刷新状态", "Refresh state"))}
                        </Button>
                        <Button variant="outline" onClick={rescan} disabled={loading || busy}>
                            {t(lt("重新扫描", "Rescan"))}
                        </Button>
                    </>
                }
            />

            <StatusNotice
                tone="info"
                title={lt("插件配置回到 OpenClaw 控制台", "Plugin setup lives in the OpenClaw console")}
                description={lt("这个页面现在只保留连接设置、宿主健康、最近入站证明和控制台入口。需要安装插件、填写渠道参数或完成 wizard 时，请优先进入 OpenClaw 控制台。", "This page only keeps connection settings, host health, recent inbound proof, and console links. Use the OpenClaw console for plugin install, channel config, and wizard flows.")}
            />

            {meta ? <SourceMetaRow source={meta.source} savePath={meta.savePath} reloadRequired={meta.reloadRequired} /> : null}
            {error ? <StatusNotice tone="warning" title={lt("读取状态失败", "Failed to load state")} description={error} /> : null}
            {!error && startupState === "refreshing" ? (
                <StatusNotice
                    tone="info"
                    title={lt("PluginHostRuntime 正在后台刷新", "PluginHostRuntime is refreshing in the background")}
                    description={t(lt(`当前先展示${snapshotFreshness === "cached" ? "缓存" : "最小"}快照，完整宿主状态会在后台刷新完成后自动切换。${snapshot?.lastRefreshAt ? ` 最近一次刷新：${snapshot.lastRefreshAt}` : ""}`, `Showing a ${snapshotFreshness === "cached" ? "cached" : "minimal"} snapshot first. The full host state will replace it after background refresh.${snapshot?.lastRefreshAt ? ` Last refresh: ${snapshot.lastRefreshAt}` : ""}`))}
                />
            ) : null}
            {!error && startupState === "error" ? (
                <StatusNotice
                    tone="warning"
                    title={lt("PluginHostRuntime 后台刷新失败", "PluginHostRuntime refresh failed")}
                description={snapshot?.lastRefreshError || t(lt("最近一次后台刷新失败，当前继续展示缓存快照。", "The latest background refresh failed. The UI is still showing a cached snapshot."))}
            />
        ) : null}

            {snapshot?.refreshInFlight || snapshot?.lastLiveRefreshAt || snapshot?.lastDeepRefreshAt ? (
                <StatusNotice
                    tone={snapshot?.refreshInFlight ? "info" : "success"}
                    title={snapshot?.refreshInFlight ? lt("PluginHostRuntime 正在后台快刷", "PluginHostRuntime is doing a live refresh") : lt("当前展示的是最新宿主快照", "Showing the latest host snapshot")}
                    description={[
                        snapshot?.refreshInFlight ? t(lt("页面轮询现在只刷新宿主快照，不再顺带触发重型插件目录探测。", "Polling now refreshes only the host snapshot and no longer drags deep inventory scans along.")) : "",
                        snapshot?.lastLiveRefreshAt ? t(lt(`最近快刷：${snapshot.lastLiveRefreshAt}`, `Last live refresh: ${snapshot.lastLiveRefreshAt}`)) : "",
                        snapshot?.lastDeepRefreshAt ? t(lt(`最近深刷：${snapshot.lastDeepRefreshAt}`, `Last deep refresh: ${snapshot.lastDeepRefreshAt}`)) : "",
                    ].filter(Boolean).join(" · ")}
                />
            ) : null}

            <DomainSummaryStrip
                items={[
                    { label: t(lt("刷新状态", "Refresh")), value: t(startupState === "ready" ? lt("已就绪", "Ready") : startupState === "refreshing" ? lt("后台刷新中", "Refreshing") : startupState === "error" ? lt("刷新失败", "Failed") : lt("冷启动", "Cold start")), description: [t(snapshotFreshness === "live" ? lt("当前展示 live 快照。", "Showing a live snapshot.") : lt("当前展示缓存或最小快照。", "Showing a cached or minimal snapshot.")), snapshot?.refreshInFlight ? t(lt("后台快刷进行中。", "Live refresh in flight.")) : "", snapshot?.lastLiveRefreshAt ? t(lt(`最近快刷：${snapshot.lastLiveRefreshAt}`, `Last live refresh: ${snapshot.lastLiveRefreshAt}`)) : ""].filter(Boolean).join(" ") },
                    { label: t(lt("宿主模式", "Host mode")), value: t(runtimeConfig.hostMode === "external" ? lt("外部 OpenClaw host", "External host") : lt("连接本地 OpenClaw", "Local OpenClaw")), description: t(runtimeConfig.hostMode === "external" ? lt("V8 不再维护本地状态目录。", "V8 no longer manages a local state root.") : lt("默认示例目录是 ~/.openclaw。", "The default sample root is ~/.openclaw.")) },
                    { label: "Gateway", value: t(gatewayLabel(host?.gatewayHealth?.runtime?.status)), description: host?.gatewayHealth?.runtime?.detail || t(lt("当前 OpenClaw 数据面状态。", "Current OpenClaw data-plane status.")) },
                    { label: "RPC", value: t(host?.gatewayHealth?.rpc?.ok ? lt("已连通", "Connected") : lt("未就绪", "Not ready")), description: host?.gatewayHealth?.rpc?.error || t(lt("控制面与数据面是否可用。", "Whether the control plane and data plane are available.")) },
                    { label: t(lt("真实入站", "Inbound")), value: t(ownershipLabel(host?.inboundOwnership)), description: proof?.reason || t(lt("这里显示最近真实入站是否已经切到 V8 主链。", "Shows whether recent live inbound has shifted to the V8 core path.")) },
                ]}
            />

            <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,0.84fr)_minmax(0,1.16fr)]">
                <div className="grid content-start gap-6">
                    <Card className="self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("宿主连接设置", "Host connection"))}</CardTitle>
                            <CardDescription>{t(lt("Windows 官方安装示例：<code>iwr -useb https://openclaw.ai/install.ps1 | iex</code>。普通运行热路径不会自动拉起 OpenClaw；请先手动把它跑起来。只有 doctor / repair 会按需代你重启 gateway。", "Windows install example: `iwr -useb https://openclaw.ai/install.ps1 | iex`. Normal runtime paths do not auto-start OpenClaw; start it manually first. Only doctor / repair may restart the gateway for you."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">{t(lt("启用 PluginHostRuntime", "Enable PluginHostRuntime"))}</div>
                                        <div className="text-xs leading-5 text-slate-500">{t(lt("关闭后 V8 不再接管 channels runtime，但不会改动 OpenClaw 自己的配置。", "When off, V8 stops owning channel runtime state but does not change OpenClaw's own config."))}</div>
                                    </div>
                                    <Switch checked={config.enabled} onCheckedChange={(checked) => setConfig((current) => ({ ...current, enabled: checked }))} />
                                </div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">{t(lt("启动时刷新插件根", "Refresh on startup"))}</div>
                                        <div className="text-xs leading-5 text-slate-500">{t(lt("影响 V8 的插件发现，不替代 OpenClaw 自己的控制台与配置。", "Affects plugin discovery in V8. It does not replace the OpenClaw console or config."))}</div>
                                    </div>
                                    <Switch checked={config.scanOnStartup} onCheckedChange={(checked) => setConfig((current) => ({ ...current, scanOnStartup: checked }))} />
                                </div>
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="plugin-host-mode">{t(lt("OpenClaw 宿主模式", "OpenClaw host mode"))}</Label>
                            <select
                                id="plugin-host-mode"
                                value={config.hostMode}
                                onChange={(event) => setConfig((current) => ({ ...current, hostMode: event.target.value === "external" ? "external" : "managed_local" }))}
                                className="flex h-11 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-slate-400 focus:ring-2 focus:ring-slate-200"
                            >
                                <option value="managed_local">{t(lt("连接本地 OpenClaw（需先手动启动）", "Connect to local OpenClaw (start it manually first)"))}</option>
                                <option value="external">{t(lt("接入外部 OpenClaw host", "Use external OpenClaw host"))}</option>
                            </select>
                        </div>

                        {config.hostMode === "managed_local" ? (
                            <>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-root">{t(lt("OpenClaw 状态目录", "OpenClaw state root"))}</Label>
                                    <Input id="openclaw-root" value={config.managedLocal.rootDir} onChange={(event) => setConfig((current) => ({ ...current, managedLocal: { ...current.managedLocal, rootDir: event.target.value } }))} />
                                    <p className="text-xs leading-5 text-slate-500">{t(lt("官方默认就是 <code>~/.openclaw</code>。V8 会在这里读取 <code>openclaw.json</code> 和插件状态。", "The official default is `~/.openclaw`. V8 reads `openclaw.json` and plugin state from here."))}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-tooling">{t(lt("CLI / tooling 根目录", "CLI / tooling root"))}</Label>
                                    <Input id="openclaw-tooling" value={config.managedLocal.toolingRoot} onChange={(event) => setConfig((current) => ({ ...current, managedLocal: { ...current.managedLocal, toolingRoot: event.target.value } }))} />
                                    <p className="text-xs leading-5 text-slate-500">{t(lt("留空时优先使用系统 PATH 里的 <code>openclaw</code>。只有你用本地前缀或自定义目录时才需要填。", "Leave blank to prefer `openclaw` from system PATH. Fill this only when you use a local prefix or custom tooling root."))}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-launcher">{t(lt("手动启动脚本（可选）", "Manual launcher (optional)"))}</Label>
                                    <Input id="openclaw-launcher" value={config.managedLocal.launcherPath} onChange={(event) => setConfig((current) => ({ ...current, managedLocal: { ...current.managedLocal, launcherPath: event.target.value } }))} />
                                    <p className="text-xs leading-5 text-slate-500">{t(lt("如果你平时有固定的 gateway 启动脚本，可以填这里。V8 只解释来源，不再自动做父子托管。", "If you use a fixed gateway launcher, place it here. V8 only records the source and does not parent-manage the process."))}</p>
                                </div>
                            </>
                        ) : (
                            <>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-base-url">{t(lt("外部 host 控制面地址", "External control URL"))}</Label>
                                    <Input id="openclaw-base-url" value={config.externalHost.baseUrl} onChange={(event) => setConfig((current) => ({ ...current, externalHost: { ...current.externalHost, baseUrl: event.target.value } }))} />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-gateway-url">{t(lt("外部 gateway 数据面地址", "External gateway URL"))}</Label>
                                    <Input id="openclaw-gateway-url" value={config.externalHost.gatewayBaseUrl} onChange={(event) => setConfig((current) => ({ ...current, externalHost: { ...current.externalHost, gatewayBaseUrl: event.target.value } }))} />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="openclaw-auth-token">{t(lt("认证 Token（可选）", "Auth token (optional)"))}</Label>
                                    <Input id="openclaw-auth-token" value={config.externalHost.authToken} onChange={(event) => setConfig((current) => ({ ...current, externalHost: { ...current.externalHost, authToken: event.target.value } }))} />
                                </div>
                            </>
                        )}

                        <Button onClick={save} disabled={busy}>
                            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("保存连接设置", "Save connection"))}
                        </Button>
                    </CardContent>
                    </Card>

                    <Card className="self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("精简插件列表", "Plugin list"))}</CardTitle>
                            <CardDescription>{t(lt("这里只保留 V8 关心的运行时事实：插件是不是 active、会话类型是什么、support tier 是什么，以及真实入站有没有切到 V8。", "This list only keeps the runtime facts V8 actually needs: plugin activity, chat types, support tier, and whether live inbound has switched to V8."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="max-h-[460px] space-y-4 overflow-y-auto pr-1">
                            {(snapshot?.plugins || []).length ? (
                                (snapshot?.plugins || []).map((plugin) => (
                                    <div key={plugin.pluginId} className="rounded-2xl border border-slate-200 bg-white p-4">
                                        <div className="flex flex-wrap items-start justify-between gap-3">
                                            <div className="space-y-2">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <div className="text-base font-medium text-slate-900">{plugin.displayName || plugin.pluginId}</div>
                                                    <Badge variant="outline" className="border-slate-200 bg-white text-slate-600">{plugin.pluginId}</Badge>
                                                    {plugin.lifecycleState ? <Badge variant="outline">{plugin.lifecycleState}</Badge> : null}
                                                    {plugin.healthState ? <Badge variant="outline">{plugin.healthState}</Badge> : null}
                                                </div>
                                                <div className="grid gap-1 text-xs leading-5 text-slate-600">
                                                    <div><span className="font-medium text-slate-900">{t(lt("支持层级：", "Support:"))}</span>{t(supportLabel(plugin.supportTier))}</div>
                                                    <div><span className="font-medium text-slate-900">{t(lt("激活 / 安装：", "Activation / setup:"))}</span>{plugin.activationState || "unknown"} / {plugin.setupState || "unknown"}</div>
                                                    <div><span className="font-medium text-slate-900">{t(lt("会话类型：", "Chat types:"))}</span>{(plugin.transportCapabilities?.chatTypes || []).join(" / ") || t(lt("未声明", "Unset"))}</div>
                                                    <div><span className="font-medium text-slate-900">{t(lt("支持群聊：", "Group chat:"))}</span>{plugin.transportCapabilities?.groupSupported ? t(lt("是", "Yes")) : t(lt("否", "No"))}</div>
                                                    <div><span className="font-medium text-slate-900">{t(lt("首次接入方式：", "First-time onboarding:"))}</span>{plugin.transportCapabilities?.onboardingType || t(lt("未声明", "Unset"))}</div>
                                                    {plugin.pluginType === "channel" ? (
                                                        <div><span className="font-medium text-slate-900">{t(lt("接入证据：", "Evidence:"))}</span>{(plugin.channelSurface?.evidence || []).join(" / ") || t(lt("暂未观察到", "Not observed yet"))}</div>
                                                    ) : null}
                                                    <div><span className="font-medium text-slate-900">{t(lt("family adapter：", "Family adapter:"))}</span>{plugin.familyAdapterReady ? t(lt("已就绪", "Ready")) : t(lt("未就绪", "Not ready"))}</div>
                                                </div>
                                            </div>
                                            <Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
                                                {plugin.onboardingCompleted
                                                    ? t(lt("已完成首次接入", "Initial onboarding complete"))
                                                    : plugin.supportTier === "transport-hosted" && String(host?.inboundOwnership || "").trim().toLowerCase() === "v8_owned"
                                                    ? t(lt("V8 已接住真实入站", "V8 owns live inbound"))
                                                    : plugin.supportTier === "tool-bridged"
                                                      ? t(lt("V8 可编排该插件工具", "Tools orchestrated by V8"))
                                                    : t(lt("在 OpenClaw 控制台配置", "Configure in OpenClaw"))}
                                            </Badge>
                                        </div>
                                        {plugin.unavailableReasons?.length ? (
                                            <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                                                {plugin.unavailableReasons.slice(0, 2).map((reason) => <div key={reason}>{reason}</div>)}
                                            </div>
                                        ) : null}
                                    </div>
                                ))
                            ) : (
                                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-8 text-sm text-slate-500">
                                    {t(lt("当前还没有扫描到插件。请先在 OpenClaw 控制台里安装插件，或确认 `managedLocal.rootDir` 指向的是正在使用的 OpenClaw 状态目录。", "No plugins were scanned yet. Install them in the OpenClaw console first, or confirm `managedLocal.rootDir` points to the live OpenClaw state root."))}
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    <Card className="self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("OpenClaw 控制台", "OpenClaw console"))}</CardTitle>
                            <CardDescription>{t(lt("如果需要安装插件、填写 Discord / Weixin / Feishu 的接入参数，或者继续 wizard / pairing，请直接回到 OpenClaw 控制台。", "Return to the OpenClaw console when you need to install plugins, configure Discord / Weixin / Feishu, or continue wizard / pairing flows."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap gap-3">
                                {control?.dashboardUrl ? (
                                    <Button asChild variant="outline" size="sm">
                                        <a href={control.dashboardUrl} target="_blank" rel="noreferrer">
                                            {t(lt("打开 OpenClaw 控制台", "Open console"))}
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </a>
                                    </Button>
                                ) : null}
                                {control?.configUrl ? (
                                    <Button asChild variant="outline" size="sm">
                                        <a href={control.configUrl} target="_blank" rel="noreferrer">
                                            {t(lt("打开 Config 页", "Open config"))}
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </a>
                                    </Button>
                                ) : null}
                                {control?.docsUrl ? (
                                    <Button asChild variant="outline" size="sm">
                                        <a href={control.docsUrl} target="_blank" rel="noreferrer">
                                            {t(lt("打开官方文档", "Open docs"))}
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </a>
                                    </Button>
                                ) : null}
                            </div>
                            <div className="space-y-2 text-xs leading-5 text-slate-500">
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("控制台：", "Console:"))}</span>{control?.dashboardUrl || t(lt("未声明", "Unset"))}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">Config：</span>{control?.configUrl || t(lt("未声明", "Unset"))}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("文档：", "Docs:"))}</span>{control?.docsUrl || t(lt("未声明", "Unset"))}</div>
                            </div>
                            {!host?.gatewayHealth?.rpc?.ok ? (
                                <StatusNotice tone="warning" title={lt("如果控制台打不开，请先确认 OpenClaw 已经手动启动", "If the console is unreachable, confirm OpenClaw is started manually")} description={lt("推荐先跑 `openclaw onboard --install-daemon`，再用 `openclaw gateway status` 确认数据面与控制台已经可访问。", "Run `openclaw onboard --install-daemon` first, then use `openclaw gateway status` to confirm both the gateway and console are reachable.")} />
                            ) : null}
                        </CardContent>
                    </Card>
                </div>

                <div className="grid content-start gap-6">
                    <Card className="order-2 self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("工具目录与筛选预览", "Tool inventory and selection preview"))}</CardTitle>
                            <CardDescription>{t(lt("上半部分是 OpenClaw/plugin_host 工具目录健康；下半部分只是诊断预览。真实任务候选会在收到用户消息后，由 extensions runtime 按当前上下文动态生成。", "The first section shows OpenClaw/plugin_host inventory health; the second section is only a diagnostic preview. Real task candidates are generated dynamically by the extensions runtime after a user message arrives."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="max-h-[860px] space-y-5 overflow-y-auto pr-1">
                            {extensionsMeta ? <SourceMetaRow source={extensionsMeta.source} savePath={extensionsMeta.savePath} reloadRequired={extensionsMeta.reloadRequired} /> : null}
                            <div className="grid gap-4 md:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                                <div className="space-y-4">
                                    <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                        <div className="flex items-center justify-between gap-4">
                                            <div className="space-y-1">
                                                <div className="text-sm font-medium text-slate-900">{t(lt("启用工具树预筛", "Enable tree prefilter"))}</div>
                                                <div className="text-xs leading-5 text-slate-500">{t(lt("关闭后只保留 lexical 候选池与 allowed_tools 过滤。", "When off, only the lexical pool and allowed_tools filtering remain."))}</div>
                                            </div>
                                            <Switch
                                                checked={prefilterEnabled}
                                                onCheckedChange={(checked) => setExtensionsConfig((current) => ({
                                                    ...current,
                                                    prefilterPolicy: { ...(current.prefilterPolicy || {}), enabled: checked, mode: "llm_tree" },
                                                }))}
                                            />
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <Label htmlFor="plugin-host-prefilter">{t(lt("专用预筛模型", "Dedicated prefilter model"))}</Label>
                                        <Select
                                            value={prefilterModel || "__empty__"}
                                            onValueChange={(value: string) => setExtensionsConfig((current) => ({
                                                ...current,
                                                modelBindings: {
                                                    ...(current.modelBindings || {}),
                                                    prefilterModel: value === "__empty__" ? "" : value,
                                                },
                                            }))}
                                        >
                                            <SelectTrigger id="plugin-host-prefilter" className="w-full">
                                                <SelectValue placeholder={t(lt("未指定，回退 lexical", "Unset, fall back to lexical"))} />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="__empty__">{t(lt("未指定，回退 lexical", "Unset, fall back to lexical"))}</SelectItem>
                                                {prefilterModels.map((model) => (
                                                    <SelectItem key={modelValue(model)} value={modelValue(model)}>
                                                        {modelLabel(model)}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <p className="text-xs leading-5 text-slate-500">{t(lt("建议绑定廉价通用 LLM，例如 deepseek-chat / deepseek-v3。未指定时当前直接回退 lexical。", "Bind a low-cost general LLM such as deepseek-chat / deepseek-v3. If unset, the runtime falls back to lexical directly."))}</p>
                                    </div>

                                    <div className="flex flex-wrap gap-3">
                                        <Button onClick={saveToolSelection} disabled={toolConfigBusy}>
                                            {toolConfigBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                            {t(lt("保存工具筛选", "Save tool selection"))}
                                        </Button>
                                        <Button variant="outline" onClick={refreshToolInventory} disabled={toolCatalogBusy}>
                                            {toolCatalogBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                            {t(lt("刷新目录健康", "Refresh inventory"))}
                                        </Button>
                                    </div>
                                </div>

                                <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="font-medium text-slate-900">{t(lt("当前目录策略", "Current strategy"))}</span>
                                        <Badge variant={selectionMode === "llm_tree" ? "default" : selectionMode === "fallback" ? "secondary" : "outline"}>{t(selectionModeLabel)}</Badge>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span>{t(lt("Role", "Role"))}</span>
                                        <Badge variant="outline">{toolSelection?.role || "extensions_prefilter"}</Badge>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span>{t(lt("模型", "Model"))}</span>
                                        <Badge variant="outline" className="max-w-[220px] truncate">{toolSelection?.modelId || prefilterModel || t(lt("未指定", "Unset"))}</Badge>
                                    </div>
                                    <div className="grid grid-cols-3 gap-3 text-xs">
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                            <div className="text-slate-500">{t(lt("候选池", "Pool"))}</div>
                                            <div className="mt-1 font-semibold text-slate-900">{toolSelection?.poolSize ?? 0}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                            <div className="text-slate-500">{t(lt("可调用", "Callable"))}</div>
                                            <div className="mt-1 font-semibold text-slate-900">{toolSelection?.callableSize ?? 0}</div>
                                        </div>
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                            <div className="text-slate-500">{t(lt("目录总数", "Inventory"))}</div>
                                            <div className="mt-1 font-semibold text-slate-900">{toolSelection?.inventorySize ?? 0}</div>
                                        </div>
                                    </div>
                                    {toolSelection?.timingsMs ? (
                                        <div className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-xs text-slate-500">
                                            bridge state {toolSelection.timingsMs.bridgeStateMs ?? toolSelection.timingsMs.bridgeState ?? 0}ms · engine cache {toolSelection.timingsMs.engineInventoryCacheMs ?? 0}ms · bridge tools {toolSelection.timingsMs.bridgeToolsRequestMs ?? toolSelection.timingsMs.gatewayInventory ?? 0}ms · lexical {toolSelection.timingsMs.lexicalMs ?? toolSelection.timingsMs.lexical ?? 0}ms · selection {toolSelection.timingsMs.selectionMs ?? 0}ms · total {toolSelection.timingsMs.totalMs ?? 0}ms
                                            {typeof toolSelection.timingsMs.prefilterMs === "number"
                                                ? ` · prefilter ${toolSelection.timingsMs.prefilterMs}ms`
                                                : typeof toolSelection.timingsMs.prefilter === "number"
                                                    ? ` · prefilter ${toolSelection.timingsMs.prefilter}ms`
                                                    : typeof toolSelection.timingsMs.rerank === "number"
                                                        ? ` · prefilter ${toolSelection.timingsMs.rerank}ms`
                                                    : ""}
                                        </div>
                                    ) : null}
                                    {toolSelection?.reason ? (
                                        <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-xs leading-5 text-amber-800">
                                            {toolSelection.reason}
                                        </div>
                                    ) : null}
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="plugin-host-tool-query">{t(lt("筛选预览查询（诊断）", "Selection preview query (diagnostic)"))}</Label>
                                <p className="text-xs leading-5 text-slate-500">
                                    {t(lt("这里不会改变当前会话，也不会写入 supervisor。它只用一条模拟需求测试 lexical seed、LLM 工具树预筛和 family expansion 是否工作。", "This does not change the active session or write to the supervisor. It only tests lexical seeds, LLM tree prefiltering, and family expansion with a simulated request."))}
                                </p>
                                <div className="flex gap-3">
                                    <Input
                                        id="plugin-host-tool-query"
                                        value={toolQuery}
                                        onChange={(event) => setToolQuery(event.target.value)}
                                        placeholder={t(lt("输入一条模拟用户需求，例如：查询飞书会话", "Enter a simulated user request, e.g. list Lark sessions"))}
                                    />
                                    <Button variant="outline" onClick={previewToolSelection} disabled={toolCatalogBusy || !toolQuery.trim()}>
                                        {toolCatalogBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                        {t(lt("预览", "Preview"))}
                                    </Button>
                                </div>
                            </div>

                            {toolCatalogError ? (
                                <StatusNotice tone="warning" title={lt("工具目录读取失败", "Failed to load tool catalog")} description={toolCatalogError} />
                            ) : null}

                            {toolCatalog ? (
                                <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-700">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-medium text-slate-900">{t(lt("工具目录真相", "Tool inventory truth"))}</span>
                                        <Badge variant={toolCatalog.toolInventoryHealth === "healthy" ? "default" : "secondary"}>
                                            {t(toolInventoryHealthLabel(toolCatalog.toolInventoryHealth))}
                                        </Badge>
                                        <Badge variant="outline">
                                            {t(toolInventorySourceLabel(toolCatalog.toolInventorySource))}
                                        </Badge>
                                        {toolCatalog.toolInventoryFreshness ? (
                                            <Badge variant="outline">{toolCatalog.toolInventoryFreshness}</Badge>
                                        ) : null}
                                        {toolCatalog.cacheHit === true ? (
                                            <Badge variant="outline">{t(lt("热缓存命中", "Hot cache hit"))}</Badge>
                                        ) : null}
                                        {toolCatalog.backgroundRefresh ? (
                                            <Badge variant="secondary">{t(lt("后台刷新中", "Background refresh"))}</Badge>
                                        ) : null}
                                        {toolCatalog.inventoryStale ? (
                                            <Badge variant="secondary">{t(lt("目录使用旧缓存", "Serving stale inventory"))}</Badge>
                                        ) : null}
                                    </div>
                                    <div className="grid max-h-48 gap-2 overflow-y-auto pr-1 text-xs text-slate-500">
                                        <div>{t(lt("目录来源会直接决定动态插件工具能不能被 V8 看见。manifest 只能保底，动态工具必须依赖 gateway RPC 或 durable cache。", "The inventory source decides whether V8 can see dynamic plugin tools. Static manifests are only a fallback; dynamic tools require gateway RPC or the durable cache."))}</div>
                                        <div>
                                            <span className="font-medium text-slate-900">{t(lt("operator.read：", "operator.read:"))}</span>
                                            {toolCatalog.operatorReadAvailable === true
                                                ? t(lt("已可用", "Available"))
                                                : toolCatalog.operatorReadAvailable === false
                                                    ? t(lt("缺少 scope", "Scope missing"))
                                                    : t(lt("未判定", "Unknown"))}
                                        </div>
                                        {toolCatalog.toolInventoryErrors?.stateCatalogError ? (
                                            <div><span className="font-medium text-slate-900">state manifest：</span>{toolCatalog.toolInventoryErrors.stateCatalogError}</div>
                                        ) : null}
                                        {toolCatalog.toolInventoryErrors?.cliCatalogError ? (
                                            <div><span className="font-medium text-slate-900">CLI：</span>{toolCatalog.toolInventoryErrors.cliCatalogError}</div>
                                        ) : null}
                                        {toolCatalog.toolInventoryErrors?.sourceScanCatalogError ? (
                                            <div><span className="font-medium text-slate-900">source scan：</span>{toolCatalog.toolInventoryErrors.sourceScanCatalogError}</div>
                                        ) : null}
                                        {toolCatalog.toolInventoryErrors?.gatewayCatalogError ? (
                                            <div><span className="font-medium text-slate-900">gateway RPC：</span>{toolCatalog.toolInventoryErrors.gatewayCatalogError}</div>
                                        ) : null}
                                        {toolCatalog.inventoryError ? (
                                            <div><span className="font-medium text-slate-900">{t(lt("inventory 回退：", "Inventory fallback:"))}</span>{toolCatalog.inventoryError}</div>
                                        ) : null}
                                        {toolCatalog.toolInventoryTimingsMs ? (
                                            <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px] text-slate-500">
                                                {`gateway RPC ${toolCatalog.toolInventoryTimingsMs.gatewayRpcMs ?? 0}ms · durable cache ${toolCatalog.toolInventoryTimingsMs.durableCacheMs ?? 0}ms · source scan ${toolCatalog.toolInventoryTimingsMs.sourceScanMs ?? 0}ms · state manifest ${toolCatalog.toolInventoryTimingsMs.stateManifestMs ?? 0}ms · total ${toolCatalog.toolInventoryTimingsMs.totalMs ?? 0}ms`}
                                            </div>
                                        ) : null}
                                        {toolCatalog.selection?.timingsMs ? (
                                            <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2 text-[11px] text-slate-500">
                                                {`engine bridge-state ${toolCatalog.selection.timingsMs.bridgeStateMs ?? 0}ms · inventory ${toolCatalog.selection.timingsMs.engineInventoryCacheMs ?? 0}ms · selection ${toolCatalog.selection.timingsMs.selectionMs ?? 0}ms · prefilter ${toolCatalog.selection.timingsMs.prefilterMs ?? 0}ms`}
                                            </div>
                                        ) : null}
                                    </div>
                                </div>
                            ) : null}

                            <div className="space-y-3">
                                <div className="flex flex-wrap items-center gap-2">
                                    <div className="text-sm font-medium text-slate-900">{t(lt("此查询下的预览候选", "Preview candidates for this query"))}</div>
                                    {previewedToolQuery ? <Badge variant="outline" className="max-w-[360px] truncate">{previewedToolQuery}</Badge> : null}
                                    {toolCatalog?.prefilterTimedOut ? <Badge variant="secondary">{t(lt("预筛超时，已回退 lexical", "Prefilter timed out, fell back to lexical"))}</Badge> : null}
                                    {toolCatalog?.prefilterCacheHit ? <Badge variant="outline">{t(lt("预筛结果命中缓存", "Prefilter cache hit"))}</Badge> : null}
                                </div>
                                {previewExposure.length ? (
                                    <div className="max-h-80 space-y-3 overflow-y-auto pr-1">
                                        {previewExposure.map((tool) => (
                                            <div key={tool.canonicalName} className="rounded-2xl border border-slate-200 bg-white p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <div className="text-sm font-medium text-slate-900">{tool.label || tool.canonicalName}</div>
                                                    <Badge variant="outline">{tool.canonicalName}</Badge>
                                                    <Badge variant="secondary">{tool.pluginId || "gateway"}</Badge>
                                                </div>
                                                <div className="mt-2 text-xs leading-5 text-slate-500">{tool.description || t(lt("暂无描述。", "No description."))}</div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                        {previewedToolQuery
                                            ? t(lt("这条模拟查询没有筛出候选。请检查目录健康、operator.read scope 或 durable cache。", "This simulated query did not expose candidates. Check inventory health, operator.read scope, or the durable cache."))
                                            : t(lt("尚未运行诊断预览。输入一条模拟用户需求并点击“预览”，才能看到这条查询会暴露的候选。", "No diagnostic preview has run yet. Enter a simulated user request and click Preview to see candidates for that query."))}
                                    </div>
                                )}
                                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-xs leading-5 text-slate-500">
                                    {t(lt("运行时真相：真实任务不会读取这里的预览结果。supervisor 收到人类消息后，extensions runtime 会基于当轮消息、上下文、lexical seed、LLM 工具树预筛和 family expansion 重新计算候选。", "Runtime truth: real tasks do not read this preview. After a human message arrives, the extensions runtime recomputes candidates from that turn's message, context, lexical seeds, LLM tree prefiltering, and family expansion."))}
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="order-1 self-start rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("宿主健康状态", "Host health"))}</CardTitle>
                            <CardDescription>{t(lt("V8 只保留它自己关心的事实：gateway / RPC、CLI 解析、handoff readiness 和最近真实入站证明。", "V8 only keeps the facts it needs: gateway / RPC, CLI resolution, handoff readiness, and recent live inbound proof."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="max-h-[760px] space-y-3 overflow-y-auto pr-1 text-sm text-slate-700">
                            {doctorSummary ? (
                                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-medium text-slate-900">{doctorSummary.title || t(lt("bridge doctor 摘要", "Bridge doctor summary"))}</span>
                                        <Badge variant={String(doctorSummary.status || "").trim().toLowerCase() === "critical" ? "destructive" : String(doctorSummary.status || "").trim().toLowerCase() === "warning" ? "secondary" : "default"}>
                                            {t(doctorStatusLabel(doctorSummary.status))}
                                        </Badge>
                                        {typeof doctorSummary.criticalCount === "number" ? <Badge variant="outline">{t(lt(`阻断 ${doctorSummary.criticalCount}`, `Critical ${doctorSummary.criticalCount}`))}</Badge> : null}
                                        {typeof doctorSummary.warningCount === "number" ? <Badge variant="outline">{t(lt(`警告 ${doctorSummary.warningCount}`, `Warnings ${doctorSummary.warningCount}`))}</Badge> : null}
                                        {typeof doctorSummary.okCount === "number" ? <Badge variant="outline">{t(lt(`通过 ${doctorSummary.okCount}`, `OK ${doctorSummary.okCount}`))}</Badge> : null}
                                    </div>
                                    {doctorSummary.description ? (
                                        <div className="mt-2 text-xs leading-5 text-slate-500">{doctorSummary.description}</div>
                                    ) : null}
                                    {doctorSummary.checkedAt ? (
                                        <div className="mt-2 text-[11px] text-slate-400">{t(lt("最近检查：", "Last doctor run:"))}{doctorSummary.checkedAt}</div>
                                    ) : null}
                                </div>
                            ) : null}
                            {doctorChecks.length ? (
                                <details className="rounded-2xl border border-slate-200 bg-white p-4">
                                    <summary className="cursor-pointer text-sm font-medium text-slate-900">
                                        {t(lt(`Doctor 检查 ${doctorChecks.length} 项`, `Doctor checks (${doctorChecks.length})`))}
                                    </summary>
                                    <div className="mt-3 max-h-80 space-y-3 overflow-y-auto pr-1">
                                        {doctorChecks.map((check, index) => (
                                            <div key={`${check.key || "doctor-check"}-${index}`} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <div className="font-medium text-slate-900">{check.title || check.key || t(lt("未命名检查", "Unnamed check"))}</div>
                                                    <Badge variant={String(check.status || "").trim().toLowerCase() === "critical" ? "destructive" : String(check.status || "").trim().toLowerCase() === "warning" ? "secondary" : "default"}>
                                                        {t(doctorStatusLabel(check.status))}
                                                    </Badge>
                                                    {check.key ? <Badge variant="outline">{check.key}</Badge> : null}
                                                </div>
                                                {check.description ? <div className="mt-2 text-xs leading-5 text-slate-500">{check.description}</div> : null}
                                                {check.details ? <div className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap break-all rounded-xl bg-white px-3 py-2 text-xs leading-5 text-slate-600">{check.details}</div> : null}
                                            </div>
                                        ))}
                                    </div>
                                </details>
                            ) : null}
                            <div><span className="font-medium text-slate-900">{t(lt("CLI 来源：", "CLI source:"))}</span>{t(cliSourceLabel(host?.cliSource))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("tooling 模式：", "Tooling mode:"))}</span>{t(toolingModeLabel(host?.toolingMode))}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t(lt("tooling 入口：", "Tooling entry:"))}</span>{host?.toolingEntry || t(lt("未解析", "Unresolved"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("launcher：", "Launcher:"))}</span>{t(launcherSourceLabel(host?.launcherSource, host?.launcherMissing))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("生命周期权责：", "Lifecycle authority:"))}</span>{host?.lifecycleAuthority || "unknown"}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("桥接状态：", "Bridge:"))}</span>{host?.bridgeStatusStale ? t(lt("stale / 未新鲜确认", "stale / not freshly confirmed")) : host?.bridgeReady ? `ready (${host?.bridgePluginId || "unknown"})` : "unready"}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("managed channels：", "Managed channels:"))}</span>{(host?.managedChannels || []).join(" / ") || t(lt("未声明", "Unset"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("provenance：", "Provenance:"))}</span>{t(bridgeProvenanceLabel(host?.installProvenance))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("刷新面：", "Refresh mode:"))}</span>{host?.refreshMode || "hot"}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("bridge 状态来源：", "Bridge status source:"))}</span>{host?.bridgeStatusSource || t(lt("未声明", "Unset"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("bridge 状态耗时：", "Bridge status latency:"))}</span>{typeof host?.bridgeStatusMs === "number" ? `${host.bridgeStatusMs}ms` : t(lt("未记录", "Not recorded"))}</div>
                            {host?.bridgeStatusObservedAt ? (
                                <div><span className="font-medium text-slate-900">{t(lt("bridge 最近观测：", "Bridge last observed:"))}</span>{host.bridgeStatusObservedAt}</div>
                            ) : null}
                            {host?.bridgeStatusStale ? (
                                <div><span className="font-medium text-slate-900">{t(lt("bridge 状态：", "Bridge status:"))}</span>{t(lt("旧缓存 / 未确认", "Stale / not freshly confirmed"))}</div>
                            ) : null}
                            {host?.bridgeStatusError ? (
                                <div><span className="font-medium text-slate-900">{t(lt("bridge 状态错误：", "Bridge status error:"))}</span>{host.bridgeStatusError}</div>
                            ) : null}
                            <div className="break-all"><span className="font-medium text-slate-900">{t(lt("状态根：", "State dir:"))}</span>{host?.resolvedStateDir || t(lt("未解析", "Unresolved"))}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t(lt("gateway 地址：", "Gateway URL:"))}</span>{host?.gatewayBaseUrl || t(lt("未解析", "Unresolved"))}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t(lt("V8 inbound：", "V8 inbound:"))}</span>{host?.v8InboundUrl || t(lt("未解析", "Unresolved"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("plugins.allow：", "plugins.allow:"))}</span>{host?.pluginsAllowConfigured ? (host?.pluginsAllow || []).join(" / ") || t(lt("已配置但为空", "Configured but empty")) : t(lt("当前为空", "Currently empty"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("渠道来源：", "Managed channels source:"))}</span>{host?.managedChannelsSource || t(lt("未声明", "Unset"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("配置来源：", "Config source:"))}</span>{t(configSourceLabel(host?.configSource))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("handoff 配置：", "Handoff configured:"))}</span>{host?.handoffConfigured ? t(lt("已注入", "Injected")) : t(lt("缺失", "Missing"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("claim 开关：", "Claim enabled:"))}</span>{host?.claimEnabled ? t(lt("已启用", "Enabled")) : t(lt("未启用", "Disabled"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("最近 claim：", "Last claim:"))}</span>{host?.lastClaimAt || t(lt("暂未记录", "No record yet"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("最近 claim 尝试：", "Last claim attempt:"))}</span>{host?.lastClaimAttemptAt || t(lt("暂未记录", "No record yet"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("最近 claim 结果：", "Last claim outcome:"))}</span>{host?.lastClaimOutcome || t(lt("暂未记录", "No record yet"))}</div>
                            {host?.lastClaimDeclineReason ? (
                                <div><span className="font-medium text-slate-900">{t(lt("最近未接管原因：", "Last decline reason:"))}</span>{host.lastClaimDeclineReason}</div>
                            ) : null}
                            {host?.lastClaimChannel ? (
                                <div><span className="font-medium text-slate-900">{t(lt("最近 claim 渠道：", "Last claim channel:"))}</span>{host.lastClaimChannel}</div>
                            ) : null}
                            {host?.lastClaimConversation ? (
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("最近 claim 会话：", "Last claim conversation:"))}</span>{host.lastClaimConversation}</div>
                            ) : null}
                            {host?.lastClaimMessageId ? (
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("最近 claim 消息：", "Last claim message:"))}</span>{host.lastClaimMessageId}</div>
                            ) : null}
                            {host?.lastClaimPayloadShape ? (
                                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-xs leading-5 text-slate-600">
                                    <div className="font-medium text-slate-900">{t(lt("最近 claim 字段合同", "Last claim payload contract"))}</div>
                                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(host.lastClaimPayloadShape, null, 2)}</pre>
                                </div>
                            ) : null}
                            {(host?.fieldContractWarnings || []).map((warning, index) => (
                                <StatusNotice
                                    key={`field-contract-warning-${index}`}
                                    tone="warning"
                                    title={warning.title || lt("入站字段合同存在缺口", "Inbound field contract is incomplete")}
                                    description={`${warning.description || ""}${(warning.fields || []).length ? `\n${t(lt("缺失字段：", "Missing fields:"))}${(warning.fields || []).join(", ")}` : ""}`.trim()}
                                />
                            ))}
                            {host?.expectedBridgeClaimMissed ? (
                                <StatusNotice
                                    tone="warning"
                                    title={lt("Bridge 已 ready，但最近没有成功 claim 当前渠道消息", "Bridge is ready, but recent channel claims are missing")}
                                    description={lt("当前消息很可能回落到了 OpenClaw 原生 runner。请优先检查 bridge status 中的 claim attempt / outcome / decline reason，再核对当前渠道是否真的命中 managedChannels。", "Messages may be falling back to the native OpenClaw runner. Inspect the bridge claim attempt / outcome / decline reason first, then verify the active channel still matches managedChannels.")}
                                />
                            ) : null}
                            {(host?.pluginProvenanceWarnings || []).map((warning, index) => (
                                <StatusNotice
                                    key={`plugin-provenance-warning-${index}`}
                                    tone="warning"
                                    title={warning.title || lt("插件 trust / provenance 仍未稳定", "Plugin trust / provenance is still unstable")}
                                    description={`${warning.description || ""}${warning.pluginId ? `\nplugin: ${warning.pluginId}` : ""}${(warning.pluginIds || []).length ? `\nplugins: ${(warning.pluginIds || []).join(", ")}` : ""}`.trim()}
                                />
                            ))}
                            <div><span className="font-medium text-slate-900">{t(lt("handoff：", "Handoff:"))}</span>{host?.handoffReady ? "ready" : "unready"}{host?.handoffDrift ? t(lt("（最近有漂移）", " (recent drift)")) : ""}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("最近 handoff：", "Last handoff:"))}</span>{host?.lastInboundHandoffAt || t(lt("暂未记录", "No record yet"))}</div>
                            {bridgeProvenance === "global_auto_discovery" ? (
                                <StatusNotice
                                    tone="warning"
                                    title={lt("当前 bridge 仍是未追踪的全局自动发现扩展", "Bridge is still loaded as untracked global code")}
                                    description={lt("建议重新执行 `openclaw plugins install @v8-agent-os/openclaw-v8-bridge`，或在开发机上使用 `openclaw plugins install --link <repo>`，把 bridge 纳入 OpenClaw 4.8 的 canonical provenance。", "Reinstall the bridge with `openclaw plugins install @v8-agent-os/openclaw-v8-bridge`, or use `openclaw plugins install --link <repo>` in development so OpenClaw 4.8 can track canonical provenance.")}
                                />
                            ) : bridgeProvenance === "missing" ? (
                                <StatusNotice
                                    tone="warning"
                                    title={lt("当前未检测到 openclaw-v8-bridge 已安装到 OpenClaw 4.8 canonical 插件链", "openclaw-v8-bridge is missing from the OpenClaw 4.8 canonical plugin chain")}
                                    description={lt("检测到 bridge 仓存在但宿主未 install/link 时，消息会回落到 OpenClaw 原生 runner。请执行 `openclaw plugins install @v8-agent-os/openclaw-v8-bridge`，或在开发机上使用 `openclaw plugins install --link <repo>`。", "When the bridge repo exists but the host has not installed/linked it, channel traffic falls back to the native OpenClaw runner. Run `openclaw plugins install @v8-agent-os/openclaw-v8-bridge`, or use `openclaw plugins install --link <repo>` in development.")}
                                />
                            ) : host?.handoffConfigured === false || host?.lastClaimDeclineReason === "handoff_token_missing" ? (
                                <StatusNotice
                                    tone="warning"
                                    title={lt("Bridge 已安装，但当前 handoff token 尚未就绪", "Bridge is installed, but the handoff token is not ready")}
                                    description={lt("OpenClaw 4.8 下如果 bridge 没拿到 handoff token，消息会回落到 OpenClaw 原生 runner。V8 现在会自动把 token 写入 bridge 私有配置；如果这里仍显示缺失，请先刷新宿主状态并确认 gateway 已重新读取最新配置。", "When the bridge has no handoff token under OpenClaw 4.8, messages fall back to the native runner. V8 now auto-injects the token into the bridge plugin config; if it still shows missing, refresh host status and confirm the gateway has reloaded the latest config.")}
                                />
                            ) : null}
                            {proof ? (
                                <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-xs leading-5">
                                    <div><span className="font-medium text-slate-900">{t(lt("阶段：", "Stage:"))}</span>{proof.stage || t(lt("未记录", "Unset"))}</div>
                                    <div><span className="font-medium text-slate-900">{t(lt("最近入站：", "Last inbound:"))}</span>{proof.inboundObservedAt || t(lt("暂未观察到", "Not observed yet"))}</div>
                                    <div><span className="font-medium text-slate-900">run：</span>{proof.runId || t(lt("暂未生成", "Not created yet"))}</div>
                                    <div><span className="font-medium text-slate-900">push：</span>{proof.pushRunId || t(lt("暂未生成", "Not created yet"))}{proof.pushStatus ? ` (${proof.pushStatus})` : ""}</div>
                                    {proof.reason ? <div className="mt-2 text-slate-500">{proof.reason}</div> : null}
                                </div>
                            ) : null}
                        </CardContent>
                    </Card>

                </div>
            </div>

        </AdminPageShell>
    );
}
