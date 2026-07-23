"use client";
import { useState, useEffect, use, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ArrowLeft, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { ModelCardV2 } from "@/components/models/ModelCardV2";
import type { ControlPlaneModel, ControlPlanePayload, ProviderOverview } from "@/components/models/control-plane-types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";
import { getPlatformLoginPresetConfig, inferPlatformLoginPreset, PLATFORM_LOGIN_PRESETS, type PlatformLoginPreset } from "@/lib/models/provider-admin";
import { tg } from "@/i18n/admin-legacy";
interface AIModel {
  id: string;
  modelRef?: string;
  providerId: string;
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
  status: "idle" | "testing" | "success" | "warning" | "error";
  message?: string;
};
type ModelReasoningRepairStatus = {
  status: "idle" | "repairing" | "success" | "warning" | "error";
  message?: string;
};
const RETRIEVAL_MODEL_TYPES = new Set<string>(["EMBEDDING", "RERANK", "RERANKER"]);
function extractErrorText(value: unknown, fallback: string): string {
  if (typeof value === "string" && value.trim())
  return value;
  if (Array.isArray(value)) {
    const joined = value.
    map((item) => extractErrorText(item, "")).
    filter(Boolean).
    join(" · ");
    return joined || fallback;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return extractErrorText(record.error, "") ||
    extractErrorText(record.message, "") ||
    extractErrorText(record.detail, "") ||
    fallback;
  }
  return fallback;
}
async function readJsonErrorMessage(response: Response, fallback: string) {
  const data = await response.json().catch(() => null);
  const detail = extractErrorText(data?.detail, "") || extractErrorText(data?.error, "");
  return detail || fallback;
}
function describeCapabilityProbe(probe: ProviderOverview["localCapabilityProbe"] | null | undefined): string {
  if (!probe || typeof probe !== "object")
  return "";
  if (probe.status === "supported")
  return "Local vision ready";
  if (probe.status === "unsupported")
  return "Text path works, but local vision is unavailable";
  if (probe.status === "unknown")
  return "Text path works, but local vision was not detected";
  return "";
}
export default function ProviderConfigPage({ params



}: {params: Promise<{id: string;}>;}) {
  const { id } = use(params);
  const providerPath = `/api/providers/${id}`;
  const router = useRouter();
  const t = useT();
  const cachedProvider = peekAdminJsonCache<AIProvider>(providerPath);
  const [provider, setProvider] = useState<AIProvider | null>(() => cachedProvider || null);
  const [controlPlane, setControlPlane] = useState<ControlPlanePayload | null>(() => peekAdminJsonCache<ControlPlanePayload>("/api/model-control-plane") || null);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(() => {
    const cached = peekAdminJsonCache<{ modelRef?: string; modelId?: string }>("/api/settings/default-agent-model");
    return cached?.modelRef || cached?.modelId || null;
  });
  const [isLoading, setIsLoading] = useState(() => !cachedProvider);
  const [isSaving, setIsSaving] = useState(false);
  const [providerType, setProviderType] = useState<string>(() => cachedProvider?.type || "API");
  const [credentialMode, setCredentialMode] = useState<"apiKey" | "oauthFile">(() => cachedProvider?.type === "PLATFORM" ? "oauthFile" : cachedProvider?.credentialMode || "apiKey");
  const [apiStandard, setApiStandard] = useState<"openai" | "anthropic" | "gemini" | "comfyui">(() => cachedProvider?.apiStandard as "openai" | "anthropic" | "gemini" | "comfyui" || "openai");
  const [platformLoginPreset, setPlatformLoginPreset] = useState<PlatformLoginPreset>(() => cachedProvider ? inferPlatformLoginPreset({
    providerType: cachedProvider.type,
    apiStandard: cachedProvider.apiStandard,
    baseUrl: cachedProvider.baseUrl,
    oauthPath: cachedProvider.oauthPath,
    code: cachedProvider.code,
    name: cachedProvider.name
  }) : "codex");
  const [providerBaseUrl, setProviderBaseUrl] = useState(() => cachedProvider?.baseUrl || "");
  const [providerOauthPath, setProviderOauthPath] = useState(() => cachedProvider?.oauthPath || "");
  const [connectionStatusMap, setConnectionStatusMap] = useState<Record<string, ModelConnectionStatus>>({});
  const [reasoningRepairStatusMap, setReasoningRepairStatusMap] = useState<Record<string, ModelReasoningRepairStatus>>({});
  // Model Dialog State
  const [isModelDialogOpen, setIsModelDialogOpen] = useState(false);
  const [editingModel, setEditingModel] = useState<AIModel | null>(null);
  const [modelType, setModelType] = useState("TEXT");
  const { toast } = useToast();
  const fetchProvider = useCallback(async (force = false) => {
    if (!peekAdminJsonCache<AIProvider>(providerPath)) setIsLoading(true);
    try {
      const [nextProvider, nextControlPlane, defaultModel] = await Promise.all([
        fetchAdminJson<AIProvider>(providerPath, { force }),
        fetchAdminJson<ControlPlanePayload>("/api/model-control-plane", { force }),
        fetchAdminJson<{ modelRef?: string; modelId?: string }>("/api/settings/default-agent-model", { force })
      ]);
      if (nextProvider) {
        const inferredPreset = inferPlatformLoginPreset({
          providerType: nextProvider.type,
          apiStandard: nextProvider.apiStandard,
          baseUrl: nextProvider.baseUrl,
          oauthPath: nextProvider.oauthPath,
          code: nextProvider.code,
          name: nextProvider.name
        });
        setProvider(nextProvider);
        setProviderType(nextProvider.type || "API");
        setCredentialMode(nextProvider.type === "PLATFORM" ? "oauthFile" : nextProvider.credentialMode || "apiKey");
        setApiStandard(nextProvider.apiStandard as "openai" | "anthropic" | "gemini" | "comfyui" || "openai");
        setPlatformLoginPreset(inferredPreset);
        setProviderBaseUrl(nextProvider.baseUrl || "");
        setProviderOauthPath(nextProvider.oauthPath || "");
      }
      setControlPlane(nextControlPlane);
      setDefaultModelId(defaultModel.modelRef || defaultModel.modelId || null);
    }
    catch (error) {
      console.error("Error fetching provider:", error);
    } finally
    {
      setIsLoading(false);
    }
  }, [providerPath]);
  useEffect(() => {
    fetchProvider();
  }, [fetchProvider]);
  const handleSaveProvider = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!provider)
    return;
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
        await fetchProvider(true);
      }
    }
    catch (error) {
      console.error("Error saving provider:", error);
    } finally
    {
      setIsSaving(false);
    }
  };
  const handleSaveModel = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const data = Object.fromEntries(formData.entries());
    for (const key of ["contextWindow", "maxTokens"]) {
      if (data[key] === "") {
        data[key] = "";
      }
    }
    // Force providerId to current provider
    data.providerId = id;
    const url = editingModel ?
    `/api/models/${encodeURIComponent(editingModel.id)}?providerId=${encodeURIComponent(editingModel.providerId)}` :
    "/api/models";
    const method = editingModel ? "PUT" : "POST";
    const response = await fetch(url, {
      method,
      body: JSON.stringify(data),
      headers: { "Content-Type": "application/json" }
    });
    if (!response.ok) {
      const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.models.providers.id.page.kd2b2caac"));
      toast({
        variant: "destructive",
        title: t("app.admin.dashboard.models.providers.id.page.kd2b2caac"),
        description: errorMessage
      });
      return;
    }
    setIsModelDialogOpen(false);
    setEditingModel(null);
    await fetchProvider(true);
  };
  const handleDeleteModel = async (model: {
    id: string;
    providerId?: string;
  }) => {
    if (!confirm(t("app.admin.dashboard.models.providers.id.page.k6cc23e17")))
    return;
    if (!model.providerId) {
      toast({
        variant: "destructive",
        title: t("app.admin.dashboard.models.providers.id.page.kfdc39ee5"),
        description: t("app.admin.dashboard.models.providers.id.page.k7ef5fb27")
      });
      return;
    }
    const pendingToast = toast({
      title: t("app.admin.dashboard.models.providers.id.page.k80306f42"),
      description: t("app.admin.dashboard.models.providers.id.page.kd016d9bc", {
        model_id: model.id
      })
    });
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(model.id)}?providerId=${encodeURIComponent(model.providerId)}`, { method: "DELETE" });
      if (!response.ok) {
        const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.models.providers.id.page.kfdc39ee5"));
        pendingToast.update({
          id: pendingToast.id,
          variant: "destructive",
          title: t("app.admin.dashboard.models.providers.id.page.kfdc39ee5"),
          description: errorMessage
        });
        return;
      }
      setProvider((current) => current ?
      {
        ...current,
        models: current.models.filter((item) => !(item.id === model.id && item.providerId === model.providerId))
      } :
      current);
      pendingToast.update({
        id: pendingToast.id,
        title: t("app.admin.dashboard.models.providers.id.page.k55262795"),
        description: t("app.admin.dashboard.models.providers.id.page.kc353b24a", {
          model_id: model.id
        })
      });
      await fetchProvider();
    }
    catch (error) {
      pendingToast.update({
        id: pendingToast.id,
        variant: "destructive",
        title: t("app.admin.dashboard.models.providers.id.page.kfdc39ee5"),
        description: error instanceof Error ? error.message : t("app.admin.dashboard.models.providers.id.page.k52d13953")
      });
    }
  };
  const handleSetDefaultModel = async (modelRef: string, categoryKey?: string) => {
    const response = await fetch("/api/models/defaults", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelRef, category: categoryKey })
    });
    if (!response.ok) {
      const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.models.providers.id.page.kd2b2caac"));
      toast({ variant: "destructive", title: t("app.admin.dashboard.models.providers.id.page.kd2b2caac"), description: errorMessage });
      return;
    }
    if (!categoryKey || categoryKey === "text_generation") {
      setDefaultModelId(modelRef);
    }
    await fetchProvider();
  };
  const handleTestConnection = async (modelRef: string) => {
    setConnectionStatusMap((current) => ({
      ...current,
      [modelRef]: { status: "testing", message: t("app.admin.dashboard.models.providers.id.page.kdb5dbeb0") }
    }));
    try {
      const target = provider?.models.find((item) => (item.modelRef || item.id) === modelRef);
      const response = await fetch("/api/models/test-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modelRef, modelId: target?.modelId, providerId: target?.providerId || provider?.id })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data?.detail && typeof data.detail === "object" ? data.detail : {};
        const error = extractErrorText(detail?.error, "") ||
        extractErrorText(data?.detail, "") ||
        extractErrorText(data?.error, "") ||
        t("app.admin.dashboard.models.providers.id.page.k7520c6bd");
        setConnectionStatusMap((current) => ({
          ...current,
          [modelRef]: { status: "error", message: String(error) }
        }));
        return;
      }
      const protocolWarning = typeof data?.protocolWarningMessage === "string" ? data.protocolWarningMessage : "";
      const recommendedRoute = [
      data?.recommendedApiStandard ? `protocol: ${data.recommendedApiStandard}` : "",
      data?.recommendedBaseUrl ? `baseURL: ${data.recommendedBaseUrl}` : ""].
      filter(Boolean).join(" / ");
      const successMessage = [
      protocolWarning,
      `${data.providerName || "Provider"} · ${Math.round(Number(data.latencyMs || 0))}ms`, recommendedRoute, t("app.admin.dashboard.model.hub.catalog.messages.skippedCapabilities"),



      data.message || t("app.admin.dashboard.models.providers.id.page.k163942fe"),
      describeCapabilityProbe(data.capabilityProbe)].
      filter(Boolean).join(" · ");
      setConnectionStatusMap((current) => ({
        ...current,
        [modelRef]: { status: protocolWarning ? "warning" : "success", message: successMessage }
      }));
    }
    catch (error) {
      const errorMessage = error instanceof Error ? error.message : t("app.admin.dashboard.models.providers.id.page.k52d13953");
      setConnectionStatusMap((current) => ({
        ...current,
        [modelRef]: { status: "error", message: errorMessage }
      }));
    }
  };
  const handleRepairReasoning = async (modelRef: string) => {
    setReasoningRepairStatusMap((current) => ({
      ...current,
      [modelRef]: { status: "repairing", message: t("app.admin.dashboard.models.providers.id.reasoningRepair.running") }
    }));
    try {
      const target = provider?.models.find((item) => (item.modelRef || item.id) === modelRef);
      const response = await fetch("/api/models/repair-reasoning", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modelRef, modelId: target?.modelId, providerId: target?.providerId || provider?.id })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        const error = extractErrorText(data?.detail, "") ||
        extractErrorText(data?.error, "") ||
        t("app.admin.dashboard.models.providers.id.reasoningRepair.failed");
        setReasoningRepairStatusMap((current) => ({
          ...current,
          [modelRef]: { status: "error", message: String(error) }
        }));
        toast({ variant: "destructive", title: t("app.admin.dashboard.models.providers.id.reasoningRepair.failed"), description: String(error) });
        return;
      }
      const status: ModelReasoningRepairStatus["status"] = data.saveStatus === "saved" ? "success" : data.status === "no_visible_reasoning_field" || data.status === "no_reasoning_signal" ? "warning" : "success";
      const message = [
      data.matchedField ? `${t("app.admin.dashboard.models.providers.id.reasoningRepair.field")}: ${data.matchedField}` : "",
      data.saveStatus ? `${t("app.admin.dashboard.models.providers.id.reasoningRepair.saveStatus")}: ${data.saveStatus}` : "",
      data.status || ""].
      filter(Boolean).join(" · ") || t("app.admin.dashboard.models.providers.id.reasoningRepair.done");
      setReasoningRepairStatusMap((current) => ({
        ...current,
        [modelRef]: { status, message }
      }));
      toast({
        title: status === "warning" ? t("app.admin.dashboard.models.providers.id.reasoningRepair.noField") : t("app.admin.dashboard.models.providers.id.reasoningRepair.success"),
        description: message
      });
      if (data.saveStatus === "saved") {
        await fetchProvider();
      }
    }
    catch (error) {
      const errorMessage = error instanceof Error ? error.message : t("app.admin.dashboard.models.providers.id.reasoningRepair.failed");
      setReasoningRepairStatusMap((current) => ({
        ...current,
        [modelRef]: { status: "error", message: errorMessage }
      }));
    }
  };
  if (isLoading)
  return <div className="p-8">Loading...</div>;
  if (!provider)
  return <div className="p-8">Provider not found</div>;
  const platformProviderSelected = providerType === "PLATFORM";
  const activePlatformPreset = getPlatformLoginPresetConfig(platformLoginPreset);
  const oauthHint = platformProviderSelected ?
  activePlatformPreset.helpText :
  t("app.admin.dashboard.models.providers.id.page.k2daf728b");
  const controlModelsById = new Map<string, ControlPlaneModel>((controlPlane?.models || []).map((item: ControlPlaneModel) => [item.modelRef || item.id, item]));
  const providerOverviewById = new Map<string, ProviderOverview>((controlPlane?.providersOverview || []).map((item: ProviderOverview) => [item.providerId, item]));
  const providerHealth = providerOverviewById.get(provider.code) || providerOverviewById.get(provider.id) || null;
  const localProbe = providerHealth?.localCapabilityProbe;
  return <div className="p-8 space-y-8 max-w-5xl mx-auto">
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
                    <p className="text-muted-foreground">{t("app.admin.dashboard.models.providers.id.page.k9488499a")}</p>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Left Column: Provider Settings */}
                <Card className="lg:col-span-1 h-fit">
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.models.providers.id.page.k53d4d6ef")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <form key={`${provider.id}-${providerType}-${credentialMode}-${provider.oauthPath || provider.apiKey || ""}`} onSubmit={handleSaveProvider} className="space-y-4">
                            <div className="grid gap-2">
                                <Label htmlFor="name">{t("app.admin.dashboard.models.providers.id.page.k6a80aac6")}</Label>
                                <Input id="name" name="name" defaultValue={provider.name} required />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="code">{t("app.admin.dashboard.models.providers.id.page.kfe66e8fc")}</Label>
                                <Input id="code" name="code" defaultValue={provider.code} required />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="type">{t("app.admin.dashboard.models.providers.id.page.kc8dc16fa")}</Label>
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
                    name: provider.name
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
                                        <SelectItem value="LOCAL">{t("app.admin.dashboard.models.providers.id.page.k3a38334f")}</SelectItem>
                                        <SelectItem value="PLATFORM">{t("app.admin.dashboard.models.providers.id.page.k2093dbe7")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            {platformProviderSelected ? <>
                                    <input type="hidden" name="platformLoginPreset" value={platformLoginPreset} />
                                    <div className="grid gap-2">
                                        <Label htmlFor="platformLoginPreset">{t("app.admin.dashboard.models.providers.id.page.k1f6f2bda")}</Label>
                                        <Select value={platformLoginPreset} onValueChange={(value: PlatformLoginPreset) => {
                  const config = getPlatformLoginPresetConfig(value);
                  setPlatformLoginPreset(value);
                  setCredentialMode("oauthFile");
                  setApiStandard(config.apiStandard);
                  setProviderBaseUrl(config.baseUrl);
                  setProviderOauthPath(config.oauthPath);
                }}>
                                            <SelectTrigger id="platformLoginPreset">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {Object.values(PLATFORM_LOGIN_PRESETS).map((preset) => <SelectItem key={preset.id} value={preset.id}>
                                                        {preset.label}
                                                    </SelectItem>)}
                                            </SelectContent>
                                        </Select>
                                        <p className="text-xs text-muted-foreground">{activePlatformPreset.description}</p>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="apiStandardReadonly">{t("app.admin.dashboard.models.providers.id.page.k3a701154")}</Label>
                                        <Input id="apiStandardReadonly" value={apiStandard === "openai" ?
                t("app.admin.dashboard.models.providers.id.page.kdab0f774") :
                apiStandard === "anthropic" ?
                t("app.admin.dashboard.models.providers.id.page.k504d12c7") :
                apiStandard === "comfyui" ?
                "ComfyUI" :
                t("app.admin.dashboard.models.providers.id.page.k560df989")} readOnly />
                                        <input type="hidden" name="apiStandard" value={apiStandard} />
                                    </div>
                                </> : <div className="grid gap-2">
                                    <Label htmlFor="apiStandard">{t("app.admin.dashboard.models.providers.id.page.k3a701154")}</Label>
                                    <input type="hidden" name="apiStandard" value={apiStandard} />
                                    <Select value={apiStandard} onValueChange={(value: "openai" | "anthropic" | "gemini" | "comfyui") => setApiStandard(value)}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="openai">{t("app.admin.dashboard.models.providers.id.page.kdab0f774")}</SelectItem>
                                            <SelectItem value="anthropic">{t("app.admin.dashboard.models.providers.id.page.k504d12c7")}</SelectItem>
                                            <SelectItem value="gemini">{t("app.admin.dashboard.models.providers.id.page.k560df989")}</SelectItem>
                                            <SelectItem value="comfyui">ComfyUI</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>}
                            {provider.type === "LOCAL" && localProbe ? <div className="rounded-xl border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                                    <div className="font-medium text-foreground">{t("app.admin.dashboard.models.providers.id.page.kcfb25f4e")}</div>
                                    <div className="mt-1">{localProbe.message}</div>
                                    <div className="mt-1">
                                        {t("app.admin.dashboard.models.providers.id.page.kecd0ba1d")}{localProbe.modelId || t("app.admin.dashboard.models.providers.id.page.k8d99f9ee")} ·
                                        {t("app.admin.dashboard.models.providers.id.page.kfcac1949")}{localProbe.contextLength || t("app.admin.dashboard.models.providers.id.page.k76ebff7c")} ·
                                        {t("app.admin.dashboard.models.providers.id.page.k000e492f")}{localProbe.maxContextLength || t("app.admin.dashboard.models.providers.id.page.k76ebff7c")}
                                    </div>
                                </div> : null}
                            <div className="grid gap-2">
                                <Label htmlFor="baseUrl">{t("app.admin.dashboard.models.providers.id.page.k7cf2d322")}</Label>
                                <Input id="baseUrl" name="baseUrl" value={providerBaseUrl} onChange={(event) => setProviderBaseUrl(event.target.value)} placeholder="https://..." />
                            </div>
                            {!platformProviderSelected ? <div className="grid gap-2">
                                    <Label htmlFor="credentialMode">{t("app.admin.dashboard.models.providers.id.page.k1947a36f")}</Label>
                                    <input type="hidden" name="credentialMode" value={credentialMode} />
                                    <Select value={credentialMode} onValueChange={(value: "apiKey" | "oauthFile") => setCredentialMode(value)}>
                                        <SelectTrigger id="credentialMode">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="apiKey">API Key</SelectItem>
                                            <SelectItem value="oauthFile">{t("app.admin.dashboard.models.providers.id.page.ke507bb9a")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div> : <input type="hidden" name="credentialMode" value="oauthFile" />}
                            {platformProviderSelected || credentialMode === "oauthFile" ? <div className="grid gap-2">
                                    <Label htmlFor="oauthPath">{t("app.admin.dashboard.models.providers.id.page.k686313b2")}</Label>
                                    <div className="flex items-center rounded-xl border border-input bg-background">
                                        <span className="shrink-0 border-r border-border/60 px-3 text-sm text-muted-foreground">oauth:</span>
                                        <Input id="oauthPath" name="oauthPath" className="border-0 shadow-none focus-visible:ring-0" value={providerOauthPath} onChange={(event) => setProviderOauthPath(event.target.value)} placeholder={activePlatformPreset.oauthPath} />
                                    </div>
                                    <p className={`text-xs ${(platformProviderSelected ? activePlatformPreset.supportState === "preset-only" : apiStandard === "gemini") ? "text-amber-600" : "text-muted-foreground"}`}>{oauthHint}</p>
                                </div> : <div className="grid gap-2">
                                    <Label htmlFor="apiKey">API Key</Label>
                                    <Input id="apiKey" name="apiKey" type="password" defaultValue={provider.apiKey ?? ""} placeholder="sk-..." />
                                </div>}
                            <div className="grid gap-2">
                                <Label htmlFor="icon">{t("app.admin.dashboard.models.providers.id.page.kb793f253")}</Label>
                                <Input id="icon" name="icon" defaultValue={provider.icon ?? ""} placeholder="🤖" />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="description">{t("app.admin.dashboard.models.providers.id.page.k033e9333")}</Label>
                                <Input id="description" name="description" defaultValue={provider.description ?? ""} />
                            </div>
                            <Button type="submit" className="w-full" disabled={isSaving}>
                                {isSaving ? t("app.admin.dashboard.models.providers.id.page.kc225e8a3") : t("app.admin.dashboard.models.providers.id.page.k60f5db7e")}
                            </Button>
                        </form>
                    </CardContent>
                </Card>

                {/* Right Column: Models List */}
                <div className="lg:col-span-2 space-y-6">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold">{t("app.admin.dashboard.models.providers.id.page.k42eb512c")} ({provider.models?.length || 0})</h2>
                        <Button onClick={() => {setEditingModel(null);setModelType("TEXT");setIsModelDialogOpen(true);}}>
                            <Plus className="w-4 h-4 mr-2" />
                            {t("app.admin.dashboard.models.providers.id.page.k82b1063c")}
                        </Button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                        {provider.models?.map((model: any) => <ModelCardV2 key={model.id} model={{ ...model, provider: { name: provider.name, icon: provider.icon } }} controlMeta={controlModelsById.get(model.modelRef || model.id) || null} isDefault={(model.modelRef || model.id) === defaultModelId} connectionStatus={connectionStatusMap[model.modelRef || model.id] || null} reasoningRepairStatus={reasoningRepairStatusMap[model.modelRef || model.id] || null} onTestConnection={handleTestConnection} onRepairReasoning={handleRepairReasoning} onSetDefault={handleSetDefaultModel}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          onEdit={(m: any) => {setEditingModel(m);setModelType(m.type || "TEXT");setIsModelDialogOpen(true);}} onDelete={handleDeleteModel} />)}
                        {(!provider.models || provider.models.length === 0) && <div className="col-span-full text-center py-12 text-muted-foreground bg-muted/30 rounded-lg border border-dashed">
                                {t("app.admin.dashboard.models.providers.id.page.k90379b26")}
                            </div>}
                    </div>
                </div>
            </div>

            {/* Model Dialog */}
            <Dialog open={isModelDialogOpen} onOpenChange={setIsModelDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{editingModel ? t("app.admin.dashboard.models.providers.id.page.k37053cf7") : t("app.admin.dashboard.models.providers.id.page.k82b1063c")}</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleSaveModel} className="space-y-4">
                        <input type="hidden" name="providerId" value={id} />

                        <div className="grid gap-2">
                            <Label htmlFor="model-id">{t("app.admin.dashboard.models.providers.id.page.k3edcd0aa")}</Label>
                            <Input id="model-id" name="modelId" defaultValue={editingModel?.modelId} required placeholder={t("app.admin.dashboard.models.providers.id.page.k8b7ccc0e")} />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="model-type">{t("app.admin.dashboard.models.providers.id.page.kc8dc16fa")}</Label>
                            <input type="hidden" name="type" value={modelType} />
                            <Select value={modelType} onValueChange={setModelType}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="TEXT">{t("app.admin.dashboard.models.providers.id.page.kfd44de16")}</SelectItem>
                                    <SelectItem value="MULTIMODAL">{t("app.admin.dashboard.models.providers.id.page.k6223be05")}</SelectItem>
                                    <SelectItem value="EMBEDDING">{t("app.admin.dashboard.models.providers.id.page.k9b398ad1")}</SelectItem>
                                    <SelectItem value="RERANK">{t("app.admin.dashboard.models.providers.id.page.k318b19b4")}</SelectItem>
                                    <SelectItem value="MEDIA">{tg(t, "da54438b")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {modelType === "TEXT" || modelType === "MULTIMODAL" ? <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <Label htmlFor="contextWindow">{t("app.admin.dashboard.models.providers.id.page.k20e21cd2")}</Label>
                                <Input id="contextWindow" name="contextWindow" type="number" defaultValue={editingModel?.contextWindow ?? ""} placeholder={tg(t, "9cbd0194")} />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="maxTokens">{t("app.admin.dashboard.models.providers.id.page.k317345b1")}</Label>
                                <Input id="maxTokens" name="maxTokens" type="number" defaultValue={editingModel?.maxTokens ?? ""} placeholder={t("app.admin.dashboard.model.hub.page.maxTokensPlaceholder")} />
                            </div>
                        </div> : RETRIEVAL_MODEL_TYPES.has(modelType) ? <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <Label htmlFor="contextWindow">{t("app.admin.dashboard.models.providers.id.page.retrievalInputWindow")}</Label>
                                <Input id="contextWindow" name="contextWindow" type="number" defaultValue={editingModel?.contextWindow ?? ""} placeholder={t("app.admin.dashboard.models.providers.id.page.retrievalInputWindowPlaceholder")} />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="maxTokens">{t("app.admin.dashboard.models.providers.id.page.retrievalMaxTokens")}</Label>
                                <Input id="maxTokens" name="maxTokens" type="number" defaultValue={editingModel?.maxTokens ?? ""} placeholder={t("app.admin.dashboard.models.providers.id.page.retrievalMaxTokensPlaceholder")} />
                            </div>
                            <p className="col-span-2 text-xs text-muted-foreground">
                                {t("app.admin.dashboard.models.providers.id.page.retrievalInputWindowHelp")}
                            </p>
                        </div> : null}
                        {modelType === "MEDIA" ? <div className="rounded-xl border border-dashed border-border bg-muted/50 p-3 text-sm text-muted-foreground">
                            {tg(t, "b0ee49ef")}
                        </div> : null}
                        <Button type="submit" className="w-full">{t("app.admin.dashboard.models.providers.id.page.kb7dfaded")}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </div>;
}
