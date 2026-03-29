"use client";

import { useState, useEffect, use, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ArrowLeft, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { ModelCardV2 } from "@/components/models/ModelCardV2";
import type { ControlPlaneModel, ControlPlanePayload, ProviderOverview } from "@/components/models/control-plane-types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import {
    getPlatformLoginPresetConfig,
    inferPlatformLoginPreset,
    PLATFORM_LOGIN_PRESETS,
    type PlatformLoginPreset,
} from "@/lib/models/provider-admin";
import { lt } from "@/lib/locale";

interface AIModel {
    id: string;
    providerId: string;
    name: string;
    modelId: string;
    type: string;
    contextWindow: number | null;
    maxTokens: number | null;
    isEnabled: boolean;
}

interface AIProvider {
    id: string;
    name: string;
    code: string;
    description: string | null;
    icon: string | null;
    baseUrl: string | null;
    apiKey: string | null;
    type: string;
    apiStandard?: string;
    isEnabled: boolean;
    credentialMode?: "apiKey" | "oauthFile";
    hasCredential?: boolean;
    oauthPath?: string;
    oauthPathMasked?: string;
    models: AIModel[];
}

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

function describeCapabilityProbe(probe: ProviderOverview["localCapabilityProbe"] | null | undefined): string {
    if (!probe || typeof probe !== "object") return "";
    if (probe.status === "supported") return "Local vision ready";
    if (probe.status === "unsupported") return "Text path works, but local vision is unavailable";
    if (probe.status === "unknown") return "Text path works, but local vision was not detected";
    return "";
}

export default function ProviderConfigPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = use(params);
    const router = useRouter();
    const t = useT();
    const [provider, setProvider] = useState<AIProvider | null>(null);
    const [controlPlane, setControlPlane] = useState<ControlPlanePayload | null>(null);
    const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [providerType, setProviderType] = useState<string>("API");
    const [credentialMode, setCredentialMode] = useState<"apiKey" | "oauthFile">("apiKey");
    const [apiStandard, setApiStandard] = useState<"openai" | "anthropic" | "gemini">("openai");
    const [platformLoginPreset, setPlatformLoginPreset] = useState<PlatformLoginPreset>("qwenCode");
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [providerOauthPath, setProviderOauthPath] = useState("");
    const [connectionStatusMap, setConnectionStatusMap] = useState<Record<string, ModelConnectionStatus>>({});

    // Model Dialog State
    const [isModelDialogOpen, setIsModelDialogOpen] = useState(false);
    const [editingModel, setEditingModel] = useState<AIModel | null>(null);
    const { toast } = useToast();

    const fetchProvider = useCallback(async () => {
        setIsLoading(true);
        try {
            const [providerRes, controlPlaneRes, defaultModelRes] = await Promise.all([
                fetch(`/api/providers/${id}`),
                fetch("/api/model-control-plane", { cache: "no-store" }),
                fetch("/api/settings/default-agent-model"),
            ]);

            if (providerRes.ok) {
                const nextProvider = await providerRes.json();
                const inferredPreset = inferPlatformLoginPreset({
                    providerType: nextProvider.type,
                    apiStandard: nextProvider.apiStandard,
                    baseUrl: nextProvider.baseUrl,
                    oauthPath: nextProvider.oauthPath,
                    code: nextProvider.code,
                    name: nextProvider.name,
                });
                setProvider(nextProvider);
                setProviderType(nextProvider.type || "API");
                setCredentialMode(nextProvider.type === "PLATFORM" ? "oauthFile" : (nextProvider.credentialMode || "apiKey"));
                setApiStandard((nextProvider.apiStandard as "openai" | "anthropic" | "gemini") || "openai");
                setPlatformLoginPreset(inferredPreset);
                setProviderBaseUrl(nextProvider.baseUrl || "");
                setProviderOauthPath(nextProvider.oauthPath || "");
            } else {
                console.error("Failed to fetch provider");
            }

            if (controlPlaneRes.ok) {
                setControlPlane(await controlPlaneRes.json());
            }

            if (defaultModelRes.ok) {
                const data = await defaultModelRes.json();
                setDefaultModelId(data.modelId || null);
            }
        } catch (error) {
            console.error("Error fetching provider:", error);
        } finally {
            setIsLoading(false);
        }
    }, [id]);

    useEffect(() => {
        fetchProvider();
    }, [fetchProvider]);

    const handleSaveProvider = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        if (!provider) return;
        setIsSaving(true);
        const formData = new FormData(e.currentTarget);
        const data = Object.fromEntries(formData.entries());

        try {
            const res = await fetch(`/api/providers/${id}`, {
                method: "PUT",
                body: JSON.stringify({ ...data, isEnabled: provider.isEnabled }),
                headers: { "Content-Type": "application/json" }
            });
            if (res.ok) {
                await fetchProvider();
            }
        } catch (error) {
            console.error("Error saving provider:", error);
        } finally {
            setIsSaving(false);
        }
    };

    const handleSaveModel = async (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        const data = Object.fromEntries(formData.entries());

        // Force providerId to current provider
        data.providerId = id;

        const url = editingModel
            ? `/api/models/${encodeURIComponent(editingModel.id)}?providerId=${encodeURIComponent(editingModel.providerId)}`
            : "/api/models";
        const method = editingModel ? "PUT" : "POST";

        await fetch(url, {
            method,
            body: JSON.stringify(data),
            headers: { "Content-Type": "application/json" }
        });

        setIsModelDialogOpen(false);
        setEditingModel(null);
        fetchProvider(); // Refresh to see new model
    };

    const handleDeleteModel = async (model: { id: string; providerId?: string }) => {
        if (!confirm(t(lt("确认删除这个模型吗？", "Delete this model?")))) return;
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
            setProvider((current) => current
                ? {
                    ...current,
                    models: current.models.filter((item) => !(item.id === model.id && item.providerId === model.providerId)),
                }
                : current);
            pendingToast.update({
                id: pendingToast.id,
                title: t(lt("删除模型成功", "Model removed")),
                description: t(lt(`${model.id} 已从供应商目录移除。`, `${model.id} was removed from the provider catalog.`)),
            });
            await fetchProvider();
        } catch (error) {
            pendingToast.update({
                id: pendingToast.id,
                variant: "destructive",
                title: t(lt("删除模型失败", "Failed to remove model")),
                description: error instanceof Error ? error.message : t(lt("未知错误", "Unknown error")),
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
                const error = data?.detail?.error || data?.error || data?.detail?.message || t(lt("连接测试失败", "Connection test failed"));
                setConnectionStatusMap((current) => ({
                    ...current,
                    [modelId]: { status: "error", message: String(error) },
                }));
                return;
            }
            const successMessage = [
                `${data.providerName || "Provider"} · ${Math.round(Number(data.latencyMs || 0))}ms`,
                data.message || t(lt("API 可用", "API available")),
                describeCapabilityProbe(data.capabilityProbe),
            ].filter(Boolean).join(" · ");
            setConnectionStatusMap((current) => ({
                ...current,
                [modelId]: { status: "success", message: successMessage },
            }));
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : t(lt("未知错误", "Unknown error"));
            setConnectionStatusMap((current) => ({
                ...current,
                [modelId]: { status: "error", message: errorMessage },
            }));
        }
    };

    if (isLoading) return <div className="p-8">Loading...</div>;
    if (!provider) return <div className="p-8">Provider not found</div>;
    const platformProviderSelected = providerType === "PLATFORM";
    const activePlatformPreset = getPlatformLoginPresetConfig(platformLoginPreset);
    const oauthHint = platformProviderSelected
            ? activePlatformPreset.helpText
            : apiStandard === "gemini"
            ? t(lt("Gemini 当前仍使用原生 API Key 调用。若需要 Gemini CLI OAuth，请把类型切到“平台”并选择 Gemini CLI 预设。", "Gemini still uses native API keys here. Switch to Platform and choose the Gemini CLI preset if you need Gemini CLI OAuth."))
            : t(lt("适用于 Qwen OAuth、Codex auth.json 等 access_token 文件。保存时会自动复制到 ~/.v8-agent-os/core/oauth/providers 并写回标准 oauth: 引用。", "Use this for Qwen OAuth, Codex auth.json, and other access_token files. Saved files are copied into ~/.v8-agent-os/core/oauth/providers and referenced with the standard oauth: format."));

    const controlModelsById = new Map<string, ControlPlaneModel>(
        (controlPlane?.models || []).map((item: ControlPlaneModel) => [item.modelId, item])
    );
    const providerOverviewById = new Map<string, ProviderOverview>(
        (controlPlane?.providersOverview || []).map((item: ProviderOverview) => [item.providerId, item])
    );
    const providerHealth = providerOverviewById.get(provider.code) || providerOverviewById.get(provider.id) || null;
    const localProbe = providerHealth?.localCapabilityProbe;

    return (
        <div className="p-8 space-y-8 max-w-5xl mx-auto">
            {/* Header */}
            <div className="flex items-center gap-4">
                <Button variant="ghost" size="icon" onClick={() => router.back()}>
                    <ArrowLeft className="w-4 h-4" />
                </Button>
                <div>
                    <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                        <span className="text-3xl">{provider.icon}</span>
                        {provider.name}
                    </h1>
                    <p className="text-muted-foreground">{t(lt("配置 API 连接信息和模型参数。", "Configure API access and model parameters."))}</p>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Left Column: Provider Settings */}
                <Card className="lg:col-span-1 h-fit">
                    <CardHeader>
                        <CardTitle>{t(lt("提供商设置", "Provider settings"))}</CardTitle>
                        <CardDescription>{t(lt("配置 API 连接信息。", "Configure the provider connection settings."))}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <form key={`${provider.id}-${providerType}-${credentialMode}-${provider.oauthPath || provider.apiKey || ""}`} onSubmit={handleSaveProvider} className="space-y-4">
                            <div className="grid gap-2">
                                <Label htmlFor="name">{t(lt("名称", "Name"))}</Label>
                                <Input id="name" name="name" defaultValue={provider.name} required />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="code">{t(lt("代码 (Code)", "Code"))}</Label>
                                <Input id="code" name="code" defaultValue={provider.code} required />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="type">{t(lt("类型", "Type"))}</Label>
                                <input type="hidden" name="type" value={providerType} />
                                <Select value={providerType} onValueChange={(value) => {
                                    setProviderType(value);
                                    if (value === "PLATFORM") {
                                        const preset = inferPlatformLoginPreset({
                                            providerType: value,
                                            apiStandard,
                                            baseUrl: providerBaseUrl,
                                            oauthPath: providerOauthPath,
                                            code: provider.code,
                                            name: provider.name,
                                        });
                                        const config = getPlatformLoginPresetConfig(preset);
                                        setCredentialMode("oauthFile");
                                        setPlatformLoginPreset(preset);
                                        setApiStandard(config.apiStandard);
                                        setProviderBaseUrl(config.baseUrl);
                                        setProviderOauthPath(providerOauthPath || config.oauthPath);
                                    }
                                }}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="API">API</SelectItem>
                                        <SelectItem value="LOCAL">{t(lt("本地 (Local)", "Local"))}</SelectItem>
                                        <SelectItem value="PLATFORM">{t(lt("平台", "Platform"))}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            {platformProviderSelected ? (
                                <>
                                    <input type="hidden" name="platformLoginPreset" value={platformLoginPreset} />
                                    <div className="grid gap-2">
                                        <Label htmlFor="platformLoginPreset">{t(lt("平台登录", "Platform login"))}</Label>
                                        <Select
                                            value={platformLoginPreset}
                                            onValueChange={(value: PlatformLoginPreset) => {
                                                const config = getPlatformLoginPresetConfig(value);
                                                setPlatformLoginPreset(value);
                                                setCredentialMode("oauthFile");
                                                setApiStandard(config.apiStandard);
                                                setProviderBaseUrl(config.baseUrl);
                                                setProviderOauthPath(config.oauthPath);
                                            }}
                                        >
                                            <SelectTrigger id="platformLoginPreset">
                                                <SelectValue />
                                            </SelectTrigger>
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
                                    <div className="grid gap-2">
                                        <Label htmlFor="apiStandardReadonly">{t(lt("API 格式", "API format"))}</Label>
                                        <Input
                                            id="apiStandardReadonly"
                                            value={
                                                apiStandard === "openai"
                                                    ? t(lt("OpenAI 兼容", "OpenAI-compatible"))
                                                    : apiStandard === "anthropic"
                                                        ? t(lt("Anthropic 兼容", "Anthropic-compatible"))
                                                        : t(lt("Gemini 原生", "Gemini native"))
                                            }
                                            readOnly
                                        />
                                        <input type="hidden" name="apiStandard" value={apiStandard} />
                                    </div>
                                </>
                            ) : (
                                <div className="grid gap-2">
                                    <Label htmlFor="apiStandard">{t(lt("API 格式", "API format"))}</Label>
                                    <input type="hidden" name="apiStandard" value={apiStandard} />
                                    <Select value={apiStandard} onValueChange={(value: "openai" | "anthropic" | "gemini") => setApiStandard(value)}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="openai">{t(lt("OpenAI 兼容", "OpenAI-compatible"))}</SelectItem>
                                            <SelectItem value="anthropic">{t(lt("Anthropic 兼容", "Anthropic-compatible"))}</SelectItem>
                                            <SelectItem value="gemini">{t(lt("Gemini 原生", "Gemini native"))}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            )}
                            {provider.type === "LOCAL" && localProbe ? (
                                <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                    <div className="font-medium text-foreground">{t(lt("本地视觉状态", "Local vision status"))}</div>
                                    <div className="mt-1">{localProbe.message}</div>
                                    <div className="mt-1">
                                        {t(lt("模型：", "Model:"))}{localProbe.modelId || t(lt("未识别", "Unknown"))} ·
                                        {t(lt("上下文：", "Context:"))}{localProbe.contextLength || t(lt("未知", "Unknown"))} ·
                                        {t(lt("最大窗口：", "Max window:"))}{localProbe.maxContextLength || t(lt("未知", "Unknown"))}
                                    </div>
                                </div>
                            ) : null}
                            <div className="grid gap-2">
                                <Label htmlFor="baseUrl">{t(lt("基础 URL", "Base URL"))}</Label>
                                <Input
                                    id="baseUrl"
                                    name="baseUrl"
                                    value={providerBaseUrl}
                                    onChange={(event) => setProviderBaseUrl(event.target.value)}
                                    placeholder="https://..."
                                />
                            </div>
                            {!platformProviderSelected ? (
                                <div className="grid gap-2">
                                    <Label htmlFor="credentialMode">{t(lt("认证方式", "Credential mode"))}</Label>
                                    <input type="hidden" name="credentialMode" value={credentialMode} />
                                    <Select value={credentialMode} onValueChange={(value: "apiKey" | "oauthFile") => setCredentialMode(value)}>
                                        <SelectTrigger id="credentialMode">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="apiKey">API Key</SelectItem>
                                            <SelectItem value="oauthFile">{t(lt("OAuth 文件", "OAuth file"))}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            ) : (
                                <input type="hidden" name="credentialMode" value="oauthFile" />
                            )}
                            {platformProviderSelected || credentialMode === "oauthFile" ? (
                                <div className="grid gap-2">
                                    <Label htmlFor="oauthPath">{t(lt("OAuth 文件路径", "OAuth file path"))}</Label>
                                    <div className="flex items-center rounded-xl border border-input bg-background">
                                        <span className="shrink-0 border-r border-border/60 px-3 text-sm text-muted-foreground">oauth:</span>
                                        <Input
                                            id="oauthPath"
                                            name="oauthPath"
                                            className="border-0 shadow-none focus-visible:ring-0"
                                            value={providerOauthPath}
                                            onChange={(event) => setProviderOauthPath(event.target.value)}
                                            placeholder={activePlatformPreset.oauthPath}
                                        />
                                    </div>
                                    <p className={`text-xs ${(platformProviderSelected ? activePlatformPreset.supportState === "preset-only" : apiStandard === "gemini") ? "text-amber-600" : "text-muted-foreground"}`}>{oauthHint}</p>
                                </div>
                            ) : (
                                <div className="grid gap-2">
                                    <Label htmlFor="apiKey">API Key</Label>
                                    <Input id="apiKey" name="apiKey" type="password" defaultValue={provider.apiKey ?? ""} placeholder="sk-..." />
                                </div>
                            )}
                            <div className="grid gap-2">
                                <Label htmlFor="icon">{t(lt("图标 (Emoji)", "Icon (emoji)"))}</Label>
                                <Input id="icon" name="icon" defaultValue={provider.icon ?? ""} placeholder="🤖" />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="description">{t(lt("描述", "Description"))}</Label>
                                <Input id="description" name="description" defaultValue={provider.description ?? ""} />
                            </div>
                            <Button type="submit" className="w-full" disabled={isSaving}>
                                {isSaving ? t(lt("保存中...", "Saving...")) : t(lt("保存更改", "Save changes"))}
                            </Button>
                        </form>
                    </CardContent>
                </Card>

                {/* Right Column: Models List */}
                <div className="lg:col-span-2 space-y-6">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold">{t(lt("关联模型", "Linked models"))} ({provider.models?.length || 0})</h2>
                        <Button onClick={() => { setEditingModel(null); setIsModelDialogOpen(true); }}>
                            <Plus className="w-4 h-4 mr-2" />
                            {t(lt("添加模型", "Add model"))}
                        </Button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                        {provider.models?.map((model: any) => (
                            <ModelCardV2
                                key={model.id}
                                model={{ ...model, provider: { name: provider.name, icon: provider.icon } }}
                                controlMeta={controlModelsById.get(model.modelId) || null}
                                isDefault={model.modelId === defaultModelId}
                                connectionStatus={connectionStatusMap[model.modelId] || null}
                                onTestConnection={handleTestConnection}
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                onEdit={(m: any) => { setEditingModel(m); setIsModelDialogOpen(true); }}
                                onDelete={handleDeleteModel}
                            />
                        ))}
                        {(!provider.models || provider.models.length === 0) && (
                            <div className="col-span-full text-center py-12 text-muted-foreground bg-muted/30 rounded-lg border border-dashed">
                                {t(lt("暂无模型。", "No models yet."))}
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Model Dialog */}
            <Dialog open={isModelDialogOpen} onOpenChange={setIsModelDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{editingModel ? t(lt("编辑模型", "Edit model")) : t(lt("添加模型", "Add model"))}</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleSaveModel} className="space-y-4">
                        <input type="hidden" name="providerId" value={id} />

                        <div className="grid gap-2">
                            <Label htmlFor="model-name">{t(lt("显示名称", "Display name"))}</Label>
                            <Input id="model-name" name="name" defaultValue={editingModel?.name} required placeholder={t(lt("例如：GPT-4 Turbo", "e.g. GPT-4 Turbo"))} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="model-id">{t(lt("模型 ID (API)", "Model ID (API)"))}</Label>
                            <Input id="model-id" name="modelId" defaultValue={editingModel?.modelId} required placeholder={t(lt("例如：gpt-4-turbo-preview", "e.g. gpt-4-turbo-preview"))} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="model-type">{t(lt("类型", "Type"))}</Label>
                            <Select name="type" defaultValue={editingModel?.type || "TEXT"}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="TEXT">{t(lt("文本 (Text)", "Text"))}</SelectItem>
                                    <SelectItem value="MULTIMODAL">{t(lt("多模态 (Multimodal)", "Multimodal"))}</SelectItem>
                                    <SelectItem value="EMBEDDING">{t(lt("向量学习 (Embedding)", "Embedding"))}</SelectItem>
                                    <SelectItem value="RERANK">{t(lt("语义重排 (Rerank)", "Rerank"))}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <Label htmlFor="contextWindow">{t(lt("上下文窗口", "Context window"))}</Label>
                                <Input id="contextWindow" name="contextWindow" type="number" defaultValue={editingModel?.contextWindow ?? ""} placeholder="128000" />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="maxTokens">{t(lt("最大 Token", "Max tokens"))}</Label>
                                <Input id="maxTokens" name="maxTokens" type="number" defaultValue={editingModel?.maxTokens ?? ""} placeholder="4096" />
                            </div>
                        </div>
                        <Button type="submit" className="w-full">{t(lt("保存模型", "Save model"))}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
}
