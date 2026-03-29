"use client";

import { useEffect, useMemo, useState } from "react";
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
    lastRefreshAt?: string | null;
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
        unavailableReasons?: string[] | null;
        transportCapabilities?: { chatTypes?: string[] | null; groupSupported?: boolean; onboardingType?: string | null } | null;
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
    rerankPolicy?: { enabled?: boolean };
    modelBindings?: { rerankerModel?: string; fallbackRerankerModel?: string };
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
    poolSize?: number;
    inventorySize?: number;
    callableSize?: number;
    timingsMs?: Record<string, number> | null;
};

type BridgeToolCatalog = {
    selection?: BridgeToolSelection | null;
    exposure?: BridgeToolEntry[];
    inventory?: BridgeToolEntry[];
};

const DEFAULT_CONFIG: RuntimeConfig = {
    enabled: true,
    scanOnStartup: true,
    hostMode: "managed_local",
    allowedFamilies: ["channel", "plugin"],
    managedLocal: { rootDir: "~/.openclaw", toolingRoot: "", launcherPath: "", autoStart: true },
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

export function PluginHostWorkbench() {
    const { toast } = useToast();
    const t = useT();
    const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
    const [config, setConfig] = useState<RuntimeConfig>(DEFAULT_CONFIG);
    const [meta, setMeta] = useState<Pick<ConfigRegistryEnvelope, "source" | "savePath" | "reloadRequired"> | null>(null);
    const [extensionsMeta, setExtensionsMeta] = useState<Pick<ConfigRegistryEnvelope, "source" | "savePath" | "reloadRequired"> | null>(null);
    const [extensionsConfig, setExtensionsConfig] = useState<ExtensionsConfigData>({ rerankPolicy: { enabled: false }, modelBindings: { rerankerModel: "", fallbackRerankerModel: "" } });
    const [rerankModels, setRerankModels] = useState<SysModel[]>([]);
    const [toolCatalog, setToolCatalog] = useState<BridgeToolCatalog | null>(null);
    const [toolQuery, setToolQuery] = useState("mind status rollback");
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [toolConfigBusy, setToolConfigBusy] = useState(false);
    const [toolCatalogBusy, setToolCatalogBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [toolCatalogError, setToolCatalogError] = useState<string | null>(null);

    async function loadToolCatalog(query: string, refresh = false) {
        setToolCatalogError(null);
        try {
            const nextCatalog = await readJson<{
                selection?: BridgeToolSelection | null;
                exposure?: BridgeToolEntry[];
                inventory?: BridgeToolEntry[];
            }>(`/api/plugin-host/bridge/tools?query=${encodeURIComponent(query)}&limit=8${refresh ? "&refresh=true" : ""}`);
            setToolCatalog({
                selection: nextCatalog.selection || null,
                exposure: Array.isArray(nextCatalog.exposure) ? nextCatalog.exposure : [],
                inventory: Array.isArray(nextCatalog.inventory) ? nextCatalog.inventory : [],
            });
        } catch (catalogError) {
            setToolCatalog(null);
            setToolCatalogError(messageFrom(catalogError, t(lt("读取工具目录失败。", "Failed to load bridge tools."))));
        }
    }

    async function load(quiet = false) {
        if (!quiet) setLoading(true);
        setError(null);
        try {
            const nextSnapshot = await readJson<Snapshot>("/api/plugin-host");
            const [domain, extensionDomain, modelList] = await Promise.all([
                fetchConfigDomain<DomainData>("plugin-host").catch(() => null),
                fetchConfigDomain<ExtensionsConfigData>("extensions").catch(() => null),
                fetch("/api/models", { cache: "no-store" })
                    .then((response) => response.json().catch(() => []))
                    .catch(() => []),
            ]);
            setSnapshot(nextSnapshot);
            setConfig(domain?.data?.config || nextSnapshot.runtimeConfig || DEFAULT_CONFIG);
            setMeta(domain ? { source: domain.source, savePath: domain.savePath, reloadRequired: domain.reloadRequired } : null);
            setExtensionsConfig(
                extensionDomain?.data || {
                    rerankPolicy: { enabled: false },
                    modelBindings: { rerankerModel: "", fallbackRerankerModel: "" },
                },
            );
            setExtensionsMeta(
                extensionDomain
                    ? { source: extensionDomain.source, savePath: extensionDomain.savePath, reloadRequired: extensionDomain.reloadRequired }
                    : null,
            );
            setRerankModels(
                Array.isArray(modelList)
                    ? modelList.filter((model: SysModel) => ["RERANK", "RERANKER"].includes(String(model?.type || "").toUpperCase()))
                    : [],
            );
            await loadToolCatalog(toolQuery, quiet);
        } catch (loadError) {
            setError(messageFrom(loadError, t(lt("读取 PluginHostRuntime 状态失败。", "Failed to load PluginHostRuntime state."))));
        } finally {
            setLoading(false);
            setBusy(false);
        }
    }

    useEffect(() => {
        void load();
    }, []);

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
                    rerankPolicy: { enabled: Boolean(extensionsConfig.rerankPolicy?.enabled) },
                    modelBindings: { rerankerModel: String(extensionsConfig.modelBindings?.rerankerModel || "").trim() },
                },
            });
            setExtensionsConfig(next.data || extensionsConfig);
            setExtensionsMeta({ source: next.source, savePath: next.savePath, reloadRequired: next.reloadRequired });
            toast({
                title: t(lt("工具筛选已保存", "Tool selection saved")),
                description: t(lt("PluginHostRuntime 的工具候选会继续写回 extensions 的 canonical source。", "PluginHostRuntime keeps writing tool selection back to the extensions canonical source.")),
            });
            await loadToolCatalog(toolQuery, true);
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
        await load(true);
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

    async function refreshToolSelection() {
        setToolCatalogBusy(true);
        await loadToolCatalog(toolQuery, true);
        setToolCatalogBusy(false);
    }

    const host = snapshot?.hostSurface;
    const control = snapshot?.controlSurface;
    const proof = host?.recentInboundProof;
    const runtimeConfig = snapshot?.runtimeConfig || config;
    const startupState = String(snapshot?.startupState || "cold").trim().toLowerCase();
    const snapshotFreshness = String(snapshot?.snapshotFreshness || "cached").trim().toLowerCase();
    const rerankEnabled = Boolean(extensionsConfig.rerankPolicy?.enabled);
    const rerankerModel = String(extensionsConfig.modelBindings?.rerankerModel || "").trim();
    const fallbackRerankerModel = String(extensionsConfig.modelBindings?.fallbackRerankerModel || "").trim();
    const toolSelection = toolCatalog?.selection || null;
    const selectionMode = String(toolSelection?.mode || "lexical").trim().toLowerCase();
    const selectionModeLabel = useMemo(() => {
        if (selectionMode === "rerank") return lt("Rerank 精排", "Rerank");
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

            <DomainSummaryStrip
                items={[
                    { label: t(lt("刷新状态", "Refresh")), value: t(startupState === "ready" ? lt("已就绪", "Ready") : startupState === "refreshing" ? lt("后台刷新中", "Refreshing") : startupState === "error" ? lt("刷新失败", "Failed") : lt("冷启动", "Cold start")), description: t(snapshotFreshness === "live" ? lt("当前展示 live 快照。", "Showing a live snapshot.") : lt("当前展示缓存或最小快照。", "Showing a cached or minimal snapshot.")) },
                    { label: t(lt("宿主模式", "Host mode")), value: t(runtimeConfig.hostMode === "external" ? lt("外部 OpenClaw host", "External host") : lt("连接本地 OpenClaw", "Local OpenClaw")), description: t(runtimeConfig.hostMode === "external" ? lt("V8 不再维护本地状态目录。", "V8 no longer manages a local state root.") : lt("默认示例目录是 ~/.openclaw。", "The default sample root is ~/.openclaw.")) },
                    { label: "Gateway", value: t(gatewayLabel(host?.gatewayHealth?.runtime?.status)), description: host?.gatewayHealth?.runtime?.detail || t(lt("当前 OpenClaw 数据面状态。", "Current OpenClaw data-plane status.")) },
                    { label: "RPC", value: t(host?.gatewayHealth?.rpc?.ok ? lt("已连通", "Connected") : lt("未就绪", "Not ready")), description: host?.gatewayHealth?.rpc?.error || t(lt("控制面与数据面是否可用。", "Whether the control plane and data plane are available.")) },
                    { label: t(lt("真实入站", "Inbound")), value: t(ownershipLabel(host?.inboundOwnership)), description: proof?.reason || t(lt("这里显示最近真实入站是否已经切到 V8 主链。", "Shows whether recent live inbound has shifted to the V8 core path.")) },
                ]}
            />

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
                    <Card className="rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("宿主连接设置", "Host connection"))}</CardTitle>
                            <CardDescription>{t(lt("Windows 官方安装示例：<code>iwr -useb https://openclaw.ai/install.ps1 | iex</code>。安装完成后，先手动把 OpenClaw 跑起来，再让 V8 连接它。", "Windows install example: `iwr -useb https://openclaw.ai/install.ps1 | iex`. Start OpenClaw first, then let V8 connect to it."))}</CardDescription>
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

                <div className="grid gap-6">
                    <Card className="rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("工具筛选", "Tool selection"))}</CardTitle>
                            <CardDescription>{t(lt("PluginHostRuntime 的工具目录先做 lexical 候选池，再优先使用 extensions_reranker，最后才回退到全局 reranker 或 lexical。这里改的是 extensions 的 canonical source，不会创建第二份配置。", "PluginHostRuntime builds a lexical candidate pool first, then prefers extensions_reranker, and only falls back to the global reranker or lexical. This still writes to the extensions canonical source."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-5">
                            {extensionsMeta ? <SourceMetaRow source={extensionsMeta.source} savePath={extensionsMeta.savePath} reloadRequired={extensionsMeta.reloadRequired} /> : null}
                            <div className="grid gap-4 md:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                                <div className="space-y-4">
                                    <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                        <div className="flex items-center justify-between gap-4">
                                            <div className="space-y-1">
                                                <div className="text-sm font-medium text-slate-900">{t(lt("启用工具精排", "Enable rerank"))}</div>
                                                <div className="text-xs leading-5 text-slate-500">{t(lt("关闭后只保留 lexical 候选池与 allowed_tools 过滤。", "When off, only the lexical pool and allowed_tools filtering remain."))}</div>
                                            </div>
                                            <Switch
                                                checked={rerankEnabled}
                                                onCheckedChange={(checked) => setExtensionsConfig((current) => ({
                                                    ...current,
                                                    rerankPolicy: { ...(current.rerankPolicy || {}), enabled: checked },
                                                }))}
                                            />
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <Label htmlFor="plugin-host-reranker">{t(lt("专用 reranker 模型", "Dedicated reranker"))}</Label>
                                        <Select
                                            value={rerankerModel || "__empty__"}
                                            onValueChange={(value: string) => setExtensionsConfig((current) => ({
                                                ...current,
                                                modelBindings: {
                                                    ...(current.modelBindings || {}),
                                                    rerankerModel: value === "__empty__" ? "" : value,
                                                },
                                            }))}
                                        >
                                            <SelectTrigger id="plugin-host-reranker" className="w-full">
                                                <SelectValue placeholder={t(lt("未指定，回退全局 reranker", "Unset, use global reranker"))} />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="__empty__">{t(lt("未指定，回退全局 reranker", "Unset, use global reranker"))}</SelectItem>
                                                {rerankModels.map((model) => (
                                                    <SelectItem key={modelValue(model)} value={modelValue(model)}>
                                                        {modelLabel(model)}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <p className="text-xs leading-5 text-slate-500">{t(lt(`当前全局回退模型：${fallbackRerankerModel || "未指定"}。`, `Current global fallback model: ${fallbackRerankerModel || "Unset"}.`))}</p>
                                    </div>

                                    <div className="flex flex-wrap gap-3">
                                        <Button onClick={saveToolSelection} disabled={toolConfigBusy}>
                                            {toolConfigBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                            {t(lt("保存工具筛选", "Save tool selection"))}
                                        </Button>
                                        <Button variant="outline" onClick={refreshToolSelection} disabled={toolCatalogBusy}>
                                            {toolCatalogBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                            {t(lt("刷新筛选真相", "Refresh selection"))}
                                        </Button>
                                    </div>
                                </div>

                                <div className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm text-slate-700">
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="font-medium text-slate-900">{t(lt("当前目录策略", "Current strategy"))}</span>
                                        <Badge variant={selectionMode === "rerank" ? "default" : selectionMode === "fallback" ? "secondary" : "outline"}>{t(selectionModeLabel)}</Badge>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span>{t(lt("Role", "Role"))}</span>
                                        <Badge variant="outline">{toolSelection?.role || "extensions_reranker"}</Badge>
                                    </div>
                                    <div className="flex items-center justify-between gap-3">
                                        <span>{t(lt("模型", "Model"))}</span>
                                        <Badge variant="outline" className="max-w-[220px] truncate">{toolSelection?.modelId || rerankerModel || fallbackRerankerModel || t(lt("未指定", "Unset"))}</Badge>
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
                                            bridge {toolSelection.timingsMs.bridgeState ?? 0}ms · inventory {toolSelection.timingsMs.gatewayInventory ?? 0}ms · lexical {toolSelection.timingsMs.lexical ?? 0}ms
                                            {typeof toolSelection.timingsMs.rerank === "number" ? ` · rerank ${toolSelection.timingsMs.rerank}ms` : ""}
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
                                <Label htmlFor="plugin-host-tool-query">{t(lt("筛选预览查询", "Selection preview query"))}</Label>
                                <div className="flex gap-3">
                                    <Input
                                        id="plugin-host-tool-query"
                                        value={toolQuery}
                                        onChange={(event) => setToolQuery(event.target.value)}
                                        placeholder={t(lt("例如：mind status rollback", "For example: mind status rollback"))}
                                    />
                                    <Button variant="outline" onClick={refreshToolSelection} disabled={toolCatalogBusy}>
                                        {t(lt("预览", "Preview"))}
                                    </Button>
                                </div>
                            </div>

                            {toolCatalogError ? (
                                <StatusNotice tone="warning" title={lt("工具目录读取失败", "Failed to load tool catalog")} description={toolCatalogError} />
                            ) : null}

                            <div className="space-y-3">
                                <div className="text-sm font-medium text-slate-900">{t(lt("当前暴露给 Supervisor 的候选", "Current exposure to Supervisor"))}</div>
                                {(toolCatalog?.exposure || []).length ? (
                                    <div className="space-y-3">
                                        {(toolCatalog?.exposure || []).map((tool) => (
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
                                        {t(lt("当前还没有暴露候选。可以先刷新筛选真相，或确认 bridge tools catalog 已经就绪。", "No exposed candidates yet. Refresh the selection preview first, or confirm the bridge tools catalog is ready."))}
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="rounded-2xl border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle>{t(lt("宿主健康状态", "Host health"))}</CardTitle>
                            <CardDescription>{t(lt("V8 只保留它自己关心的事实：gateway / RPC、CLI 解析、handoff readiness 和最近真实入站证明。", "V8 only keeps the facts it needs: gateway / RPC, CLI resolution, handoff readiness, and recent live inbound proof."))}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3 text-sm text-slate-700">
                            <div><span className="font-medium text-slate-900">{t(lt("CLI 来源：", "CLI source:"))}</span>{t(cliSourceLabel(host?.cliSource))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("tooling 模式：", "Tooling mode:"))}</span>{t(toolingModeLabel(host?.toolingMode))}</div>
                            <div className="break-all"><span className="font-medium text-slate-900">{t(lt("tooling 入口：", "Tooling entry:"))}</span>{host?.toolingEntry || t(lt("未解析", "Unresolved"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("launcher：", "Launcher:"))}</span>{t(launcherSourceLabel(host?.launcherSource, host?.launcherMissing))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("生命周期权责：", "Lifecycle authority:"))}</span>{host?.lifecycleAuthority || "unknown"}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("桥接状态：", "Bridge:"))}</span>{host?.bridgeReady ? `ready (${host?.bridgePluginId || "unknown"})` : "unready"}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("managed channels：", "Managed channels:"))}</span>{(host?.managedChannels || []).join(" / ") || t(lt("未声明", "Unset"))}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("handoff：", "Handoff:"))}</span>{host?.handoffReady ? "ready" : "unready"}{host?.handoffDrift ? t(lt("（最近有漂移）", " (recent drift)")) : ""}</div>
                            <div><span className="font-medium text-slate-900">{t(lt("最近 handoff：", "Last handoff:"))}</span>{host?.lastInboundHandoffAt || t(lt("暂未记录", "No record yet"))}</div>
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

                    <Card className="rounded-2xl border-slate-200 shadow-sm">
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
            </div>

            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle>{t(lt("精简插件列表", "Plugin list"))}</CardTitle>
                    <CardDescription>{t(lt("这里只保留 V8 关心的运行时事实：插件是不是 active、会话类型是什么、support tier 是什么，以及真实入站有没有切到 V8。", "This list only keeps the runtime facts V8 actually needs: plugin activity, chat types, support tier, and whether live inbound has switched to V8."))}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
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
                                            <div><span className="font-medium text-slate-900">{t(lt("接入类型：", "Onboarding:"))}</span>{plugin.transportCapabilities?.onboardingType || t(lt("未声明", "Unset"))}</div>
                                            <div><span className="font-medium text-slate-900">{t(lt("family adapter：", "Family adapter:"))}</span>{plugin.familyAdapterReady ? t(lt("已就绪", "Ready")) : t(lt("未就绪", "Not ready"))}</div>
                                        </div>
                                    </div>
                                    <Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
                                        {plugin.supportTier === "transport-hosted" && String(host?.inboundOwnership || "").trim().toLowerCase() === "v8_owned"
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
        </AdminPageShell>
    );
}
