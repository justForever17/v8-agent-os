"use client";

import { useEffect, useMemo, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import type { ControlPlaneModel, ProviderOverview } from "@/components/models/control-plane-types";
import { ProviderCard } from "@/components/models/ProviderCard";
import { ModelCardV2 } from "@/components/models/ModelCardV2";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { fetchConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import {
    getPlatformLoginPresetConfig,
    inferPlatformLoginPreset,
    PLATFORM_LOGIN_PRESETS,
    type PlatformLoginPreset,
} from "@/lib/models/provider-admin";
import { lt } from "@/lib/locale";

type AIProvider = {
    id: string;
    name: string;
    code: string;
    description?: string | null;
    icon?: string | null;
    baseUrl?: string | null;
    apiKey?: string | null;
    type: "API" | "LOCAL" | "PLATFORM";
    apiStandard?: string;
    isEnabled: boolean;
    credentialMode?: "apiKey" | "oauthFile";
    hasCredential?: boolean;
    oauthPath?: string;
    oauthPathMasked?: string;
    models: { id: string }[];
};

type AIModel = {
    id: string;
    providerId: string;
    name: string;
    modelId: string;
    type: string;
    contextWindow?: number | null;
    isEnabled: boolean;
    provider?: { name: string; icon?: string | null };
};

type ModelHubPayload = {
    summary?: {
        providers?: number;
        enabledProviders?: number;
        models?: number;
        rolesAssigned?: number;
    };
    models?: ControlPlaneModel[];
    providersOverview?: ProviderOverview[];
    config?: {
        governance?: {
            enabled?: boolean;
            strictCapabilityMatch?: boolean;
        };
    };
};

type ModelConnectionStatus = {
    status: "idle" | "testing" | "success" | "error";
    message?: string;
};

async function readJsonErrorMessage(response: Response, fallback: string) {
    const data = await response.json().catch(() => null);
    const detail = typeof data?.detail === "string"
        ? data.detail
        : typeof data?.detail?.error === "string"
            ? data.detail.error
            : typeof data?.error === "string"
                ? data.error
                : "";
    return detail || fallback;
}

export default function ModelHubPage() {
    const { toast } = useToast();
    const t = useT();
    const [providers, setProviders] = useState<AIProvider[]>([]);
    const [models, setModels] = useState<AIModel[]>([]);
    const [hubEnvelope, setHubEnvelope] = useState<ConfigRegistryEnvelope<ModelHubPayload> | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("all");
    const [isProviderDialogOpen, setIsProviderDialogOpen] = useState(false);
    const [isModelDialogOpen, setIsModelDialogOpen] = useState(false);
    const [editingProvider, setEditingProvider] = useState<AIProvider | null>(null);
    const [editingModel, setEditingModel] = useState<AIModel | null>(null);
    const [providerType, setProviderType] = useState<AIProvider["type"]>("API");
    const [providerCredentialMode, setProviderCredentialMode] = useState<"apiKey" | "oauthFile">("apiKey");
    const [providerApiStandard, setProviderApiStandard] = useState<"openai" | "anthropic" | "gemini">("openai");
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [providerOauthPath, setProviderOauthPath] = useState("");
    const [platformLoginPreset, setPlatformLoginPreset] = useState<PlatformLoginPreset>("qwenCode");
    const [modelType, setModelType] = useState("TEXT");
    const [modelProviderId, setModelProviderId] = useState("");
    const [connectionStatusMap, setConnectionStatusMap] = useState<Record<string, ModelConnectionStatus>>({});

    const fetchData = async () => {
        setIsLoading(true);
        try {
            const [providersRes, modelsRes, hubRes] = await Promise.all([
                fetch("/api/providers", { cache: "no-store" }),
                fetch("/api/models", { cache: "no-store" }),
                fetchConfigDomain<ModelHubPayload>("models"),
            ]);

            setProviders(providersRes.ok ? await providersRes.json() : []);
            setModels(modelsRes.ok ? await modelsRes.json() : []);
            setHubEnvelope(hubRes);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        void fetchData();
    }, []);

    const controlModelsById = useMemo(
        () => new Map((hubEnvelope?.data.models || []).map((item) => [item.modelId, item])),
        [hubEnvelope]
    );
    const providerOverviewById = useMemo(
        () => new Map((hubEnvelope?.data.providersOverview || []).map((item) => [item.providerId, item])),
        [hubEnvelope]
    );

    const filteredModels = activeTab === "all"
        ? models
        : models.filter((model) => (model.type || "").toLowerCase() === activeTab);

    const handleSaveProvider = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        const payload = Object.fromEntries(formData.entries());
        const url = editingProvider ? `/api/providers/${editingProvider.id}` : "/api/providers";
        const method = editingProvider ? "PUT" : "POST";
        await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        setIsProviderDialogOpen(false);
        setEditingProvider(null);
        await fetchData();
    };

    const platformProviderSelected = providerType === "PLATFORM";
    const activePlatformPreset = getPlatformLoginPresetConfig(platformLoginPreset);
    const oauthHint = platformProviderSelected
        ? t(activePlatformPreset.helpText)
        : providerApiStandard === "gemini"
            ? t(lt("Gemini 当前仍使用原生 API Key 调用。若需要 Gemini CLI OAuth，请切到“平台”并选择 Gemini CLI 预设。", "Gemini still uses native API keys here. Switch to Platform and choose the Gemini CLI preset if you need Gemini CLI OAuth."))
            : t(lt("适用于 Qwen OAuth、Codex auth.json 等 access_token 文件。保存时会自动复制到 ~/.v8-agent-os/core/oauth/providers 并写回标准 oauth: 引用。", "Use this for Qwen OAuth, Codex auth.json, and other access_token files. Saved files are copied into ~/.v8-agent-os/core/oauth/providers and referenced with the standard oauth: format."));

    const handleSaveModel = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        const payload = Object.fromEntries(formData.entries());
        const url = editingModel
            ? `/api/models/${encodeURIComponent(editingModel.id)}?providerId=${encodeURIComponent(editingModel.providerId)}`
            : "/api/models";
        const method = editingModel ? "PUT" : "POST";
        await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        setIsModelDialogOpen(false);
        setEditingModel(null);
        await fetchData();
    };

    const handleDeleteProvider = async (id: string) => {
        const pendingToast = toast({
            title: t(lt("删除供应商中", "Removing provider")),
            description: t(lt("正在删除供应商配置。", "Removing the provider configuration.")),
        });
        try {
            const response = await fetch(`/api/providers/${id}`, { method: "DELETE" });
            if (!response.ok) {
                const errorMessage = await readJsonErrorMessage(response, t(lt("删除供应商失败", "Failed to remove provider")));
                pendingToast.update({
                    id: pendingToast.id,
                    variant: "destructive",
                    title: t(lt("删除供应商失败", "Failed to remove provider")),
                    description: errorMessage,
                });
                return;
            }
            pendingToast.update({
                id: pendingToast.id,
                title: t(lt("删除供应商成功", "Provider removed")),
                description: t(lt("供应商配置已移除。", "The provider configuration has been removed.")),
            });
            await fetchData();
        } catch (error) {
            pendingToast.update({
                id: pendingToast.id,
                variant: "destructive",
                title: t(lt("删除供应商失败", "Failed to remove provider")),
                description: error instanceof Error ? error.message : t("未知错误"),
            });
        }
    };

    const handleDeleteModel = async (model: { id: string; providerId?: string }) => {
        if (!confirm(t(lt(`确认删除模型「${model.id}」吗？`, `Delete model "${model.id}"?`)))) {
            return;
        }
        if (!model.providerId) {
            toast({
                variant: "destructive",
                title: t(lt("删除模型失败", "Failed to remove model")),
                description: t(lt("当前模型缺少供应商归属，无法确定删除目标。", "This model has no provider binding, so the delete target cannot be resolved.")),
            });
            return;
        }
        const pendingToast = toast({
            title: t(lt("删除模型中", "Removing model")),
            description: t(lt(`正在删除 ${model.id}。`, `Removing ${model.id}.`)),
        });
        try {
            const response = await fetch(`/api/models/${encodeURIComponent(model.id)}?providerId=${encodeURIComponent(model.providerId)}`, { method: "DELETE" });
            if (!response.ok) {
                const errorMessage = await readJsonErrorMessage(response, t(lt("删除模型失败", "Failed to remove model")));
                pendingToast.update({
                    id: pendingToast.id,
                    variant: "destructive",
                    title: t(lt("删除模型失败", "Failed to remove model")),
                    description: errorMessage,
                });
                return;
            }
            setModels((current) => current.filter((item) => !(item.id === model.id && item.providerId === model.providerId)));
            pendingToast.update({
                id: pendingToast.id,
                title: t(lt("删除模型成功", "Model removed")),
                description: t(lt(`${model.id} 已从模型目录移除。`, `${model.id} was removed from the catalog.`)),
            });
            await fetchData();
        } catch (error) {
            pendingToast.update({
                id: pendingToast.id,
                variant: "destructive",
                title: t(lt("删除模型失败", "Failed to remove model")),
                description: error instanceof Error ? error.message : t("未知错误"),
            });
        }
    };

    const handleTestConnection = async (modelId: string) => {
        setConnectionStatusMap((current) => ({
            ...current,
            [modelId]: { status: "testing", message: t(lt("正在验证模型连通性", "Testing model connectivity")) },
        }));
        try {
            const response = await fetch("/api/models/test-connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ modelId }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const errorMessage = String(data?.detail?.error || data?.error || t(lt("请检查模型配置", "Check the model configuration")));
                setConnectionStatusMap((current) => ({
                    ...current,
                    [modelId]: { status: "error", message: errorMessage },
                }));
                return;
            }
            const successMessage = [
                `${data.providerName || "Provider"} · ${Math.round(Number(data.latencyMs || 0))}ms`,
                data.message || "",
            ].filter(Boolean).join(" · ");
            setConnectionStatusMap((current) => ({
                ...current,
                [modelId]: { status: "success", message: successMessage },
            }));
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : t("未知错误");
            setConnectionStatusMap((current) => ({
                ...current,
                [modelId]: { status: "error", message: errorMessage },
            }));
        }
    };

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={t(lt("模型中心", "Model hub"))}
                description={t(lt("管理供应商、模型目录和连接状态。", "Manage providers, models, and connectivity."))}
                actions={
                    <>
                        <Button variant="outline" onClick={() => void fetchData()} disabled={isLoading}>
                            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                            {t("刷新")}
                        </Button>
                        <Button onClick={() => {
                            setEditingProvider(null);
                            setProviderType("API");
                            setProviderCredentialMode("apiKey");
                            setProviderApiStandard("openai");
                            setProviderBaseUrl("");
                            setProviderOauthPath("");
                            setPlatformLoginPreset("qwenCode");
                            setIsProviderDialogOpen(true);
                        }}>
                            <Plus className="mr-2 h-4 w-4" />
                            {t(lt("添加供应商", "Add provider"))}
                        </Button>
                        <Button disabled={providers.length === 0} onClick={() => { setEditingModel(null); setModelType("TEXT"); setModelProviderId(providers[0]?.id || ""); setIsModelDialogOpen(true); }}>
                            <Plus className="mr-2 h-4 w-4" />
                            {t(lt("添加模型", "Add model"))}
                        </Button>
                    </>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: t(lt("供应商", "Providers")), value: hubEnvelope?.data.summary?.providers || providers.length, description: t(lt("已登记的供应商数量。", "Total registered providers.")) },
                    { label: t(lt("已启用供应商", "Enabled providers")), value: hubEnvelope?.data.summary?.enabledProviders || providers.filter((item) => item.isEnabled).length, description: t(lt("当前仍可用的供应商数量。", "Providers currently available for use.")) },
                    { label: t(lt("模型数量", "Models")), value: hubEnvelope?.data.summary?.models || models.length, description: t(lt("已登记的模型总数。", "Total registered models.")) },
                    { label: t(lt("规则状态", "Policy")), value: hubEnvelope?.data.config?.governance?.enabled ? t("已开启") : t("已关闭"), description: t(lt("这里显示模型规则是否生效。", "Shows whether model governance is active.")) },
                ]}
            />

            <ConfigCard title={t(lt("供应商目录", "Provider catalog"))} description={t(lt("管理供应商启用状态和连接入口。", "Manage provider enablement and connection entrypoints."))} variant="list" bodyHeight={520} bodyScroll="auto">
                {providers.length === 0 ? (
                    <EmptyState title={t(lt("还没有供应商", "No providers yet"))} description={t(lt("你可以先添加供应商，再登记模型目录。", "Add a provider first, then register models under it."))} />
                ) : (
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        {providers.map((provider) => (
                            <ProviderCard
                                key={provider.id}
                                provider={provider}
                                health={providerOverviewById.get(provider.code) || providerOverviewById.get(provider.id) || null}
                                onEdit={() => {
                                    const inferredPreset = inferPlatformLoginPreset({
                                        providerType: provider.type,
                                        apiStandard: provider.apiStandard,
                                        baseUrl: provider.baseUrl,
                                        oauthPath: provider.oauthPath,
                                        code: provider.code,
                                        name: provider.name,
                                    });
                                    setEditingProvider(provider);
                                    setProviderType(provider.type || "API");
                                    setProviderCredentialMode(provider.type === "PLATFORM" ? "oauthFile" : (provider.credentialMode || "apiKey"));
                                    setProviderApiStandard((provider.apiStandard as "openai" | "anthropic" | "gemini") || "openai");
                                    setProviderBaseUrl(provider.baseUrl || "");
                                    setProviderOauthPath(provider.oauthPath || "");
                                    setPlatformLoginPreset(inferredPreset);
                                    setIsProviderDialogOpen(true);
                                }}
                                onDelete={handleDeleteProvider}
                                onToggle={async (id, enabled) => {
                                    await fetch(`/api/providers/${id}`, {
                                        method: "PUT",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ isEnabled: enabled }),
                                    });
                                    await fetchData();
                                }}
                            />
                        ))}
                    </div>
                )}
            </ConfigCard>

            <ConfigCard title={t(lt("模型目录", "Model catalog"))} description={t(lt("查看模型能力和连接健康。", "Review model capabilities and connection health."))} variant="list" bodyHeight={520} bodyScroll="auto">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="text-sm text-slate-500">{t(lt("按能力查看当前已登记模型。", "Browse the registered models by capability."))}</div>
                    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full max-w-xl">
                        <TabsList className="grid w-full grid-cols-5 rounded-2xl bg-slate-100">
                            <TabsTrigger value="all">{t(lt("全部", "All"))}</TabsTrigger>
                            <TabsTrigger value="text">{t(lt("文本", "Text"))}</TabsTrigger>
                            <TabsTrigger value="multimodal">{t(lt("多模态", "Multimodal"))}</TabsTrigger>
                            <TabsTrigger value="embedding">{t(lt("向量", "Embedding"))}</TabsTrigger>
                            <TabsTrigger value="rerank">{t(lt("重排", "Rerank"))}</TabsTrigger>
                        </TabsList>
                    </Tabs>
                </div>

                {filteredModels.length === 0 ? (
                    <EmptyState title={t(lt("当前分类没有模型", "No models in this category"))} description={t(lt("可以切换分类，或先添加新的模型目录。", "Switch categories or add a new model to continue."))} />
                ) : (
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        {filteredModels.map((model) => (
                            <ModelCardV2
                                key={model.id}
                                model={model}
                                controlMeta={controlModelsById.get(model.modelId) || null}
                                connectionStatus={connectionStatusMap[model.modelId] || null}
                                onEdit={() => {
                                    setEditingModel(model);
                                    setModelType(model.type || "TEXT");
                                    setModelProviderId(model.providerId);
                                    setIsModelDialogOpen(true);
                                }}
                                onDelete={handleDeleteModel}
                                onTestConnection={handleTestConnection}
                            />
                        ))}
                    </div>
                )}
            </ConfigCard>

            {hubEnvelope ? (
                <SourceMetaRow
                    source={hubEnvelope.source}
                    savePath={hubEnvelope.savePath}
                    reloadRequired={hubEnvelope.reloadRequired}
                />
            ) : null}

            <Dialog open={isProviderDialogOpen} onOpenChange={setIsProviderDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{editingProvider ? t(lt("编辑供应商", "Edit provider")) : t(lt("添加供应商", "Add provider"))}</DialogTitle>
                    </DialogHeader>
                    <form key={`${editingProvider?.id || "new"}-${providerType}-${providerCredentialMode}`} onSubmit={handleSaveProvider} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="provider-name">{t(lt("供应商名称", "Provider name"))}</Label>
                            <Input id="provider-name" name="name" defaultValue={editingProvider?.name || ""} required />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="provider-code">{t(lt("供应商标识", "Provider code"))}</Label>
                            <Input id="provider-code" name="code" defaultValue={editingProvider?.code || ""} required />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="provider-type">{t(lt("供应商类型", "Provider type"))}</Label>
                            <input type="hidden" name="type" value={providerType} />
                            <Select value={providerType} onValueChange={(value: AIProvider["type"]) => {
                                setProviderType(value);
                                if (value === "PLATFORM") {
                                    const preset = editingProvider
                                        ? inferPlatformLoginPreset({
                                            providerType: value,
                                            apiStandard: providerApiStandard,
                                            baseUrl: providerBaseUrl,
                                            oauthPath: providerOauthPath,
                                            code: editingProvider.code,
                                            name: editingProvider.name,
                                        })
                                        : platformLoginPreset;
                                    const config = getPlatformLoginPresetConfig(preset);
                                    setProviderCredentialMode("oauthFile");
                                    setPlatformLoginPreset(preset);
                                    setProviderApiStandard(config.apiStandard);
                                    setProviderBaseUrl(config.baseUrl);
                                    setProviderOauthPath(providerOauthPath || config.oauthPath);
                                }
                            }}>
                                <SelectTrigger id="provider-type"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="API">API</SelectItem>
                                    <SelectItem value="LOCAL">{t(lt("本地", "Local"))}</SelectItem>
                                    <SelectItem value="PLATFORM">{t(lt("平台", "Platform"))}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {platformProviderSelected ? (
                            <>
                                <input type="hidden" name="platformLoginPreset" value={platformLoginPreset} />
                                <div className="space-y-2">
                                    <Label htmlFor="platform-login-preset">{t(lt("平台登录", "Platform login"))}</Label>
                                    <Select
                                        value={platformLoginPreset}
                                        onValueChange={(value: PlatformLoginPreset) => {
                                            const config = getPlatformLoginPresetConfig(value);
                                            setPlatformLoginPreset(value);
                                            setProviderCredentialMode("oauthFile");
                                            setProviderApiStandard(config.apiStandard);
                                            setProviderBaseUrl(config.baseUrl);
                                            setProviderOauthPath(config.oauthPath);
                                        }}
                                    >
                                        <SelectTrigger id="platform-login-preset"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {Object.values(PLATFORM_LOGIN_PRESETS).map((preset) => (
                                                <SelectItem key={preset.id} value={preset.id}>
                                                    {preset.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">{activePlatformPreset.description}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="provider-api-standard-readonly">{t(lt("API 格式", "API format"))}</Label>
                                    <Input id="provider-api-standard-readonly" value={
                                        providerApiStandard === "openai"
                                            ? t(lt("OpenAI 兼容", "OpenAI-compatible"))
                                            : providerApiStandard === "anthropic"
                                                ? t(lt("Anthropic 兼容", "Anthropic-compatible"))
                                                : t(lt("Gemini 原生", "Gemini native"))
                                    } readOnly />
                                    <input type="hidden" name="apiStandard" value={providerApiStandard} />
                                </div>
                            </>
                        ) : (
                            <div className="space-y-2">
                                <Label htmlFor="provider-api-standard">{t(lt("API 格式", "API format"))}</Label>
                                <input type="hidden" name="apiStandard" value={providerApiStandard} />
                                <Select value={providerApiStandard} onValueChange={(value: "openai" | "anthropic" | "gemini") => setProviderApiStandard(value)}>
                                    <SelectTrigger id="provider-api-standard"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="openai">{t(lt("OpenAI 兼容", "OpenAI-compatible"))}</SelectItem>
                                        <SelectItem value="anthropic">{t(lt("Anthropic 兼容", "Anthropic-compatible"))}</SelectItem>
                                        <SelectItem value="gemini">{t(lt("Gemini 原生", "Gemini native"))}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                        <div className="space-y-2">
                            <Label htmlFor="provider-base-url">{t(lt("接口地址", "Base URL"))}</Label>
                            <Input
                                id="provider-base-url"
                                name="baseUrl"
                                value={providerBaseUrl}
                                onChange={(event) => setProviderBaseUrl(event.target.value)}
                            />
                        </div>
                        {!platformProviderSelected ? (
                            <div className="space-y-2">
                                <Label htmlFor="provider-credential-mode">{t(lt("认证方式", "Credential mode"))}</Label>
                                <input type="hidden" name="credentialMode" value={providerCredentialMode} />
                                <Select value={providerCredentialMode} onValueChange={(value: "apiKey" | "oauthFile") => setProviderCredentialMode(value)}>
                                    <SelectTrigger id="provider-credential-mode"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="apiKey">API Key</SelectItem>
                                        <SelectItem value="oauthFile">{t(lt("OAuth 文件", "OAuth file"))}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        ) : (
                            <input type="hidden" name="credentialMode" value="oauthFile" />
                        )}
                        {platformProviderSelected || providerCredentialMode === "oauthFile" ? (
                            <div className="space-y-2">
                                <Label htmlFor="provider-oauth-path">{t(lt("OAuth 文件路径", "OAuth file path"))}</Label>
                                <div className="flex items-center rounded-xl border border-input bg-background">
                                    <span className="shrink-0 border-r border-border/60 px-3 text-sm text-muted-foreground">oauth:</span>
                                        <Input
                                            id="provider-oauth-path"
                                            name="oauthPath"
                                            className="border-0 shadow-none focus-visible:ring-0"
                                            value={providerOauthPath}
                                            onChange={(event) => setProviderOauthPath(event.target.value)}
                                            placeholder={activePlatformPreset.oauthPath}
                                        />
                                    </div>
                                <p className={`text-xs ${(platformProviderSelected ? activePlatformPreset.supportState === "preset-only" : providerApiStandard === "gemini") ? "text-amber-600" : "text-muted-foreground"}`}>{oauthHint}</p>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <Label htmlFor="provider-api-key">API Key</Label>
                                <Input id="provider-api-key" name="apiKey" type="password" defaultValue={editingProvider?.apiKey || ""} />
                            </div>
                        )}
                        <Button type="submit" className="w-full">{t(lt("保存供应商", "Save provider"))}</Button>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog open={isModelDialogOpen} onOpenChange={setIsModelDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{editingModel ? t(lt("编辑模型", "Edit model")) : t(lt("添加模型", "Add model"))}</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleSaveModel} className="space-y-4">
                        {providers.length === 0 ? (
                            <EmptyState title={t(lt("请先添加供应商", "Add a provider first"))} description={t(lt("模型目录必须先归属到某个供应商。", "Every model must belong to a provider."))} />
                        ) : null}
                        <div className="space-y-2">
                            <Label htmlFor="model-name">{t(lt("模型名称", "Model name"))}</Label>
                            <Input id="model-name" name="name" defaultValue={editingModel?.name || ""} required />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-provider">{t(lt("所属供应商", "Provider"))}</Label>
                            <input type="hidden" name="providerId" value={modelProviderId} />
                            <Select value={modelProviderId} onValueChange={setModelProviderId}>
                                <SelectTrigger id="model-provider"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {providers.map((provider) => (
                                        <SelectItem key={provider.id} value={provider.id}>{provider.name}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-model-id">{t(lt("模型 ID", "Model ID"))}</Label>
                            <Input id="model-model-id" name="modelId" defaultValue={editingModel?.modelId || ""} required />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-type">{t(lt("模型类型", "Model type"))}</Label>
                            <input type="hidden" name="type" value={modelType} />
                            <Select value={modelType} onValueChange={setModelType}>
                                <SelectTrigger id="model-type"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="TEXT">{t(lt("文本", "Text"))}</SelectItem>
                                    <SelectItem value="MULTIMODAL">{t(lt("多模态", "Multimodal"))}</SelectItem>
                                    <SelectItem value="EMBEDDING">{t(lt("向量", "Embedding"))}</SelectItem>
                                    <SelectItem value="RERANK">{t(lt("重排", "Rerank"))}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <Button type="submit" className="w-full">{t(lt("保存模型", "Save model"))}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </AdminPageShell>
    );
}
