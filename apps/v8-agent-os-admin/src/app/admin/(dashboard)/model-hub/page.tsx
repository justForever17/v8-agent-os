"use client";
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Plus, RefreshCw, X } from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { fetchConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { resolveModelIcon, resolveProviderLogo } from "@/lib/models/model-assets";
import { getLocalBackendPresetConfig, getPlatformLoginPresetConfig, inferPlatformLoginPreset, inferLocalBackendPreset, LOCAL_BACKEND_PRESETS, PLATFORM_LOGIN_PRESETS, type LocalBackendPreset, type PlatformLoginPreset, } from "@/lib/models/provider-admin";
type AIProvider = {
    id: string;
    name: string;
    code: string;
    description?: string | null;
    icon?: string | null;
    logoAsset?: string | null;
    baseUrl?: string | null;
    apiKey?: string | null;
    type: "API" | "LOCAL" | "PLATFORM";
    apiStandard?: string;
    isEnabled: boolean;
    credentialMode?: "apiKey" | "oauthFile";
    hasCredential?: boolean;
    oauthPath?: string;
    oauthPathMasked?: string;
    localBackendPreset?: LocalBackendPreset;
    models: {
        id: string;
    }[];
};
type AIModel = {
    id: string;
    modelRef?: string;
    providerId: string;
    modelId: string;
    type: string;
    contextWindow?: number | null;
    maxTokens?: number | null;
    rerankApiFlavor?: string;
    logoAsset?: string | null;
    isEnabled: boolean;
    provider?: {
        id?: string;
        name: string;
        icon?: string | null;
        logoAsset?: string | null;
    };
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
type CatalogModel = {
    id: string;
    modelId?: string;
    type?: string;
    contextWindow?: number | null;
    maxTokens?: number | null;
    logoAsset?: string | null;
    capabilities?: Record<string, boolean>;
};
type CatalogProvider = {
    id: string;
    name: string;
    apiStandard?: string;
    providerKind?: string;
    mediaModality?: string;
    adapter?: string;
    baseUrl?: string;
    modelsUrl?: string;
    modelsPath?: string;
    credentialRealm?: string;
    anthropicCompatible?: {
        apiStandard?: string;
        baseUrl?: string;
        messagesPath?: string;
        sourceUrl?: string;
    };
    auth?: { type?: string; path?: string };
    probeStrategy?: string;
    confidence?: string;
    sourceUrl?: string;
    logoAsset?: string | null;
    credentialHelp?: {
        label?: string;
        url?: string;
        kind?: "api_key" | "console" | "local_ui" | "docs";
        urlFrom?: "baseUrl";
    };
    isCustom?: boolean;
    singleActiveModel?: boolean;
    models?: CatalogModel[];
};
type CatalogPurpose = "chat" | "image" | "video" | "voice" | "music" | "workflow" | "model3d";
type MediaModelType = "MEDIA" | "IMAGE" | "VIDEO" | "AUDIO" | "VOICE" | "MUSIC" | "WORKFLOW" | "MODEL3D";
type CatalogRuntimeProtocol = "default" | "anthropic";

const CATALOG_PURPOSES: { id: CatalogPurpose; labelKey: string; hintKey: string; modelType: string; modality?: string }[] = [
    { id: "chat", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.chat", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.chatHint", modelType: "TEXT" },
    { id: "image", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.image", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.imageHint", modelType: "IMAGE", modality: "image" },
    { id: "video", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.video", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.videoHint", modelType: "VIDEO", modality: "video" },
    { id: "voice", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.voice", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.voiceHint", modelType: "VOICE", modality: "voice" },
    { id: "music", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.music", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.musicHint", modelType: "MUSIC", modality: "music" },
    { id: "workflow", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.workflow", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.workflowHint", modelType: "WORKFLOW", modality: "workflow" },
    { id: "model3d", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.model3d", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.model3dHint", modelType: "MODEL3D", modality: "model3d" },
];

const MEDIA_MODEL_TYPES = new Set<string>(["MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"]);

function previewModelsUrl(provider?: CatalogProvider | null): string {
    if (!provider?.baseUrl) return "";
    if (provider.modelsUrl) return provider.modelsUrl;
    const path = provider.modelsPath || "/models";
    return `${provider.baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

function isXiaomiAnthropicBaseUrl(value: string | null | undefined) {
    const normalized = String(value || "").trim().toLowerCase().replace(/\/+$/, "");
    return normalized.includes("xiaomimimo.com/anthropic") || normalized.includes("token-plan-cn.xiaomimimo.com/anthropic");
}

function getCatalogPurposeConfig(purpose: CatalogPurpose) {
    return CATALOG_PURPOSES.find((item) => item.id === purpose) || CATALOG_PURPOSES[0];
}

function normalizeModelType(value: string | null | undefined) {
    return String(value || "").trim().toUpperCase();
}

function modelMatchesTab(model: AIModel, tab: string) {
    if (tab === "all") return true;
    const type = normalizeModelType(model.type);
    if (tab === "media") return MEDIA_MODEL_TYPES.has(type);
    if (tab === "voice") return type === "VOICE" || type === "AUDIO";
    if (tab === "model3d") return type === "MODEL3D";
    return type.toLowerCase() === tab;
}

function providerMatchesPurpose(provider: CatalogProvider, purpose: CatalogPurpose) {
    const authType = provider.auth?.type;
    if (authType === "oauth_file") return false;
    const mediaModality = String(provider.mediaModality || "").toLowerCase();
    const providerKind = String(provider.providerKind || "").toLowerCase();
    const apiStandard = String(provider.apiStandard || "").toLowerCase();
    if (purpose === "chat") {
        return providerKind !== "media_generation";
    }
    const expected = getCatalogPurposeConfig(purpose).modality;
    if (purpose === "workflow") {
        return mediaModality === "workflow" || apiStandard === "comfyui" || provider.id === "comfyui";
    }
    return providerKind === "media_generation" && mediaModality === expected;
}

function getModelTypeForPurpose(purpose: CatalogPurpose) {
    return getCatalogPurposeConfig(purpose).modelType;
}
function ProviderOptionLabel({
    provider,
    suffix,
}: {
    provider: Pick<CatalogProvider, "id" | "name" | "logoAsset">;
    suffix?: string;
}) {
    const logo = resolveProviderLogo({
        providerId: provider.id,
        providerName: provider.name,
        explicitAsset: provider.logoAsset,
    });
    const initial = (provider.name || provider.id || "?").trim().slice(0, 1).toUpperCase();
    return (
        <span className="flex min-w-0 items-center gap-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-slate-100 text-[10px] font-semibold text-slate-600">
                {logo ? <img src={logo} alt="" className="h-4 w-4 object-contain" /> : initial}
            </span>
            <span className="min-w-0 truncate leading-5">{provider.name}{suffix ? ` · ${suffix}` : ""}</span>
        </span>
    );
}
function extractErrorText(value: unknown, fallback: string): string {
    if (typeof value === "string" && value.trim())
        return value;
    if (Array.isArray(value)) {
        const joined = value
            .map((item) => extractErrorText(item, ""))
            .filter(Boolean)
            .join(" · ");
        return joined || fallback;
    }
    if (value && typeof value === "object") {
        const record = value as Record<string, unknown>;
        return (extractErrorText(record.error, "")
            || extractErrorText(record.message, "")
            || extractErrorText(record.detail, "")
            || fallback);
    }
    return fallback;
}
async function readJsonErrorMessage(response: Response, fallback: string) {
    const data = await response.json().catch(() => null);
    const detail = extractErrorText(data?.detail, "") || extractErrorText(data?.error, "");
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
    const [providerApiStandard, setProviderApiStandard] = useState<"openai" | "anthropic" | "gemini" | "comfyui">("openai");
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [providerApiKey, setProviderApiKey] = useState("");
    const [providerOauthPath, setProviderOauthPath] = useState("");
    const [platformLoginPreset, setPlatformLoginPreset] = useState<PlatformLoginPreset>("geminiCli");
    const [localBackendPreset, setLocalBackendPreset] = useState<LocalBackendPreset>("ollama");
    const [modelType, setModelType] = useState("TEXT");
    const [modelProviderId, setModelProviderId] = useState("");
    const [rerankApiFlavor, setRerankApiFlavor] = useState("generic");
    const [connectionStatusMap, setConnectionStatusMap] = useState<Record<string, ModelConnectionStatus>>({});
    const [defaultModelRef, setDefaultModelRef] = useState<string | null>(null);
    const [catalogProviders, setCatalogProviders] = useState<CatalogProvider[]>([]);
    const [catalogPurpose, setCatalogPurpose] = useState<CatalogPurpose>("chat");
    const [catalogRuntimeProtocol, setCatalogRuntimeProtocol] = useState<CatalogRuntimeProtocol>("default");
    const [selectedCatalogProviderId, setSelectedCatalogProviderId] = useState("openai");
    const [catalogApiKey, setCatalogApiKey] = useState("");
    const [catalogProbeModels, setCatalogProbeModels] = useState<CatalogModel[]>([]);
    const [selectedCatalogModelId, setSelectedCatalogModelId] = useState("");
    const [catalogModelFilter, setCatalogModelFilter] = useState("");
    const [customProviderName, setCustomProviderName] = useState("");
    const [customProviderBaseUrl, setCustomProviderBaseUrl] = useState("");
    const [probedCatalogProviderId, setProbedCatalogProviderId] = useState("");
    const [catalogProbeStatus, setCatalogProbeStatus] = useState<{
        ok?: boolean;
        message?: string;
        resolvedModelsUrl?: string;
        source?: string;
        credentialSource?: string;
        usedStoredCredential?: boolean;
    } | null>(null);
    const [manualModelEntryEnabled, setManualModelEntryEnabled] = useState(false);
    const [isCatalogBusy, setIsCatalogBusy] = useState(false);
    const fetchData = async () => {
        setIsLoading(true);
        try {
            const [providersRes, modelsRes, hubRes, defaultRes, catalogRes] = await Promise.all([
                fetch("/api/providers", { cache: "no-store" }),
                fetch("/api/models", { cache: "no-store" }),
                fetchConfigDomain<ModelHubPayload>("models"),
                fetch("/api/settings/default-agent-model", { cache: "no-store" }),
                fetch("/api/models/catalog", { cache: "no-store" }),
            ]);
            setProviders(providersRes.ok ? await providersRes.json() : []);
            setModels(modelsRes.ok ? await modelsRes.json() : []);
            setHubEnvelope(hubRes);
            if (defaultRes.ok) {
                const defaultData = await defaultRes.json().catch(() => ({}));
                setDefaultModelRef(defaultData.modelRef || defaultData.modelId || null);
            }
            if (catalogRes.ok) {
                const catalogData = await catalogRes.json().catch(() => ({}));
                setCatalogProviders(Array.isArray(catalogData.providers) ? catalogData.providers : []);
            }
        }
        finally {
            setIsLoading(false);
        }
    };
    useEffect(() => {
        void fetchData();
    }, []);
    const controlModelsById = useMemo(() => new Map((hubEnvelope?.data.models || []).map((item) => [item.modelRef || item.id, item])), [hubEnvelope]);
    const providerOverviewById = useMemo(() => new Map((hubEnvelope?.data.providersOverview || []).map((item) => [item.providerId, item])), [hubEnvelope]);
    const selectedCatalogProvider = useMemo(() => catalogProviders.find((item) => item.id === selectedCatalogProviderId) || null, [catalogProviders, selectedCatalogProviderId]);
    const catalogPurposeConfig = useMemo(() => getCatalogPurposeConfig(catalogPurpose), [catalogPurpose]);
    const selectedCatalogRuntime = useMemo(() => {
        if (catalogPurpose === "chat" && catalogRuntimeProtocol === "anthropic" && selectedCatalogProvider?.anthropicCompatible?.baseUrl) {
            return {
                apiStandard: "anthropic",
                baseUrl: selectedCatalogProvider.anthropicCompatible.baseUrl,
                label: "Anthropic-compatible",
            };
        }
        return {
            apiStandard: selectedCatalogProvider?.apiStandard || (catalogPurpose === "workflow" ? "comfyui" : "openai"),
            baseUrl: selectedCatalogProvider?.baseUrl || "",
            label: selectedCatalogProvider?.apiStandard || "default",
        };
    }, [catalogPurpose, catalogRuntimeProtocol, selectedCatalogProvider]);
    const catalogPurposeLabel = t(catalogPurposeConfig.labelKey);
    const catalogPurposeHint = t(catalogPurposeConfig.hintKey);
    const selectedCredentialHelpUrl = useMemo(() => {
        const help = selectedCatalogProvider?.credentialHelp;
        if (!help) return "";
        if (help.urlFrom === "baseUrl") return selectedCatalogProvider?.baseUrl || "";
        return help.url || "";
    }, [selectedCatalogProvider]);
    const oauthCatalogProviders = useMemo(() => catalogProviders.filter((item) => item.auth?.type === "oauth_file" && catalogPurpose === "chat"), [catalogProviders, catalogPurpose]);
    const apiCatalogProviders = useMemo(() => catalogProviders.filter((item) => providerMatchesPurpose(item, catalogPurpose)), [catalogProviders, catalogPurpose]);
    const visibleCatalogModels = useMemo(() => {
        const query = catalogModelFilter.trim().toLowerCase();
        if (!query) return catalogProbeModels.slice(0, 80);
        return catalogProbeModels
            .filter((model) => `${model.modelId || ""} ${model.id || ""}`.toLowerCase().includes(query))
            .slice(0, 80);
    }, [catalogModelFilter, catalogProbeModels]);
    useEffect(() => {
        if (apiCatalogProviders.some((item) => item.id === selectedCatalogProviderId) || selectedCatalogProviderId === "__custom__") return;
        setSelectedCatalogProviderId(apiCatalogProviders[0]?.id || "__custom__");
        setCatalogProbeModels([]);
        setSelectedCatalogModelId("");
        setCatalogModelFilter("");
        setProbedCatalogProviderId("");
        setCatalogProbeStatus(null);
        setManualModelEntryEnabled(false);
        setCatalogRuntimeProtocol("default");
    }, [apiCatalogProviders, selectedCatalogProviderId]);
    const filteredModels = models.filter((model) => modelMatchesTab(model, activeTab));
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
            ? t("app.admin.dashboard.model.hub.page.k37b02ec3")
            : t("app.admin.dashboard.model.hub.page.k2daf728b");
    const localBackendConfig = getLocalBackendPresetConfig(localBackendPreset);
    const handleSaveModel = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        const payload = Object.fromEntries(formData.entries());
        for (const key of ["contextWindow", "maxTokens"]) {
            if (payload[key] === "") payload[key] = null as unknown as FormDataEntryValue;
        }
        const url = editingModel
            ? `/api/models/${encodeURIComponent(editingModel.id)}?providerId=${encodeURIComponent(editingModel.providerId)}`
            : "/api/models";
        const method = editingModel ? "PUT" : "POST";
        const response = await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.kd2b2caac"));
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.page.kd2b2caac"),
                description: errorMessage,
            });
            return;
        }
        setIsModelDialogOpen(false);
        setEditingModel(null);
        await fetchData();
    };
    const handleDeleteProvider = async (id: string) => {
        const pendingToast = toast({
            title: t("app.admin.dashboard.model.hub.page.k9f8d01b1"),
            description: t("app.admin.dashboard.model.hub.page.k73a26f43"),
        });
        try {
            const response = await fetch(`/api/providers/${id}`, { method: "DELETE" });
            if (!response.ok) {
                const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.kb2803a72"));
                pendingToast.update({
                    id: pendingToast.id,
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.page.kb2803a72"),
                    description: errorMessage,
                });
                return;
            }
            pendingToast.update({
                id: pendingToast.id,
                title: t("app.admin.dashboard.model.hub.page.k38297510"),
                description: t("app.admin.dashboard.model.hub.page.k654e3a33"),
            });
            await fetchData();
        }
        catch (error) {
            pendingToast.update({
                id: pendingToast.id,
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.page.kb2803a72"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.model.hub.page.k52d13953"),
            });
        }
    };
    const handleDeleteModel = async (model: {
        id: string;
        providerId?: string;
    }) => {
        if (!confirm(t("app.admin.dashboard.model.hub.page.k775e537a", {
            model_id: model.id
        }))) {
            return;
        }
        if (!model.providerId) {
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.page.kfdc39ee5"),
                description: t("app.admin.dashboard.model.hub.page.k7ef5fb27"),
            });
            return;
        }
        const pendingToast = toast({
            title: t("app.admin.dashboard.model.hub.page.k80306f42"),
            description: t("app.admin.dashboard.model.hub.page.kd016d9bc", {
                model_id: model.id
            }),
        });
        try {
            const response = await fetch(`/api/models/${encodeURIComponent(model.id)}?providerId=${encodeURIComponent(model.providerId)}`, { method: "DELETE" });
            if (!response.ok) {
                const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.kfdc39ee5"));
                pendingToast.update({
                    id: pendingToast.id,
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.page.kfdc39ee5"),
                    description: errorMessage,
                });
                return;
            }
            setModels((current) => current.filter((item) => !(item.id === model.id && item.providerId === model.providerId)));
            pendingToast.update({
                id: pendingToast.id,
                title: t("app.admin.dashboard.model.hub.page.k55262795"),
                description: t("app.admin.dashboard.model.hub.page.kcc45e1c7", {
                    model_id: model.id
                }),
            });
            await fetchData();
        }
        catch (error) {
            pendingToast.update({
                id: pendingToast.id,
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.page.kfdc39ee5"),
                description: error instanceof Error ? error.message : t("app.admin.dashboard.model.hub.page.k52d13953"),
            });
        }
    };
    const handleSetDefaultModel = async (modelRef: string) => {
        const response = await fetch("/api/settings/default-agent-model", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ modelRef }),
        });
        if (!response.ok) {
            const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.kd2b2caac"));
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.page.kd2b2caac"), description: errorMessage });
            return;
        }
        setDefaultModelRef(modelRef);
        await fetchData();
    };
    const handleProbeCatalogProvider = async () => {
        if (!selectedCatalogProviderId) return;
        const isCustomProvider = selectedCatalogProviderId === "__custom__";
        const isMediaPurpose = catalogPurpose !== "chat";
        const baseUrl = isCustomProvider ? customProviderBaseUrl.trim() : (selectedCatalogProvider?.baseUrl || "");
        const customAnthropicRuntime = isCustomProvider && catalogPurpose === "chat" && isXiaomiAnthropicBaseUrl(baseUrl);
        if (isCustomProvider && (!customProviderName.trim() || !baseUrl)) {
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.catalog.probeFailed"),
                description: t("app.admin.dashboard.model.hub.catalog.customMissing"),
            });
            return;
        }
        if (isCustomProvider && isMediaPurpose) {
            setCatalogProbeModels([]);
            setSelectedCatalogModelId(catalogModelFilter.trim());
            setProbedCatalogProviderId("__custom__");
            setManualModelEntryEnabled(true);
            setCatalogProbeStatus({
                ok: true,
                message: t("app.admin.dashboard.model.hub.catalog.customMediaManual", { purpose: catalogPurposeLabel }),
                source: "manual",
            });
            return;
        }
        if (customAnthropicRuntime) {
            setCatalogProbeModels([]);
            setSelectedCatalogModelId(catalogModelFilter.trim());
            setProbedCatalogProviderId("__custom__");
            setManualModelEntryEnabled(true);
            setCatalogProbeStatus({
                ok: true,
                message: t("app.admin.dashboard.model.hub.catalog.manualAnthropicBaseUrlHint"),
                source: "manual",
            });
            return;
        }
        setIsCatalogBusy(true);
        setCatalogProbeStatus(null);
        setManualModelEntryEnabled(false);
        try {
            const response = await fetch("/api/models/providers/probe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    providerId: selectedCatalogProviderId,
                    apiKey: catalogApiKey,
                    baseUrl,
                    customProviderName: isCustomProvider ? customProviderName : "",
                    providerKind: isMediaPurpose ? "media_generation" : "chat",
                    mediaModality: isMediaPurpose ? catalogPurposeConfig.modality : "",
                    apiStandard: selectedCatalogProvider?.apiStandard || (catalogPurpose === "workflow" ? "comfyui" : "openai"),
                }),
            });
            const data = await response.json().catch(() => ({}));
            const nextModels = Array.isArray(data.models) ? data.models : [];
            if (!response.ok || data.ok === false) {
                setCatalogProbeModels([]);
                setSelectedCatalogModelId("");
                setProbedCatalogProviderId("");
                const reason = extractErrorText(data.error || data.detail || data.reason, t("app.admin.dashboard.model.hub.catalog.fallback"));
                const requiresCredential = data.reason === "credential_required" || String(reason).toLowerCase().includes("api key");
                const catalogOnly = data.reason === "catalog_only_provider";
                setManualModelEntryEnabled(!requiresCredential || catalogOnly);
                setCatalogProbeStatus({
                    ok: false,
                    message: catalogOnly ? t("app.admin.dashboard.model.hub.catalog.catalogOnlyManual") : requiresCredential ? t("app.admin.dashboard.model.hub.catalog.credentialRequired") : reason,
                    resolvedModelsUrl: data.resolvedModelsUrl,
                    source: data.source,
                    credentialSource: data.credentialSource,
                    usedStoredCredential: Boolean(data.usedStoredCredential),
                });
                if (!catalogOnly) {
                    toast({
                        variant: "destructive",
                        title: t("app.admin.dashboard.model.hub.catalog.probeFailed"),
                        description: requiresCredential ? t("app.admin.dashboard.model.hub.catalog.apiKeyRequired") : t("app.admin.dashboard.model.hub.catalog.manualFallback", { reason }),
                    });
                }
                return;
            }
            const providerId = data.providerId || data.provider?.id || selectedCatalogProviderId;
            setProbedCatalogProviderId(providerId);
            setCatalogProbeModels(nextModels);
            setCatalogModelFilter("");
            setSelectedCatalogModelId(nextModels[0]?.modelId || nextModels[0]?.id || "");
            setManualModelEntryEnabled(nextModels.length === 0);
            setCatalogProbeStatus({
                ok: true,
                message: data.source === "catalog"
                    ? t("app.admin.dashboard.model.hub.catalog.catalogLoaded", { count: nextModels.length })
                    : data.usedStoredCredential
                        ? t("app.admin.dashboard.model.hub.catalog.onlineLoadedStoredCredential", { count: nextModels.length })
                        : t("app.admin.dashboard.model.hub.catalog.onlineLoaded", { count: nextModels.length }),
                resolvedModelsUrl: data.resolvedModelsUrl,
                source: data.source,
                credentialSource: data.credentialSource,
                usedStoredCredential: Boolean(data.usedStoredCredential),
            });
            if (isCustomProvider) {
                setSelectedCatalogProviderId(providerId);
                await fetchData();
            }
        }
        finally {
            setIsCatalogBusy(false);
        }
    };
    const handleConnectCatalogModel = async (providerId: string, modelId: string, apiKey = "") => {
        if (!providerId || !modelId) return;
        setIsCatalogBusy(true);
        try {
            const provider = catalogProviders.find((item) => item.id === providerId);
            const isCustomProvider = providerId === "__custom__" || selectedCatalogProviderId === "__custom__";
            const baseUrl = isCustomProvider ? customProviderBaseUrl.trim() : selectedCatalogRuntime.baseUrl || provider?.baseUrl || "";
            const isMediaPurpose = catalogPurpose !== "chat";
            const customAnthropicRuntime = isCustomProvider && catalogPurpose === "chat" && isXiaomiAnthropicBaseUrl(baseUrl);
            const response = await fetch("/api/models/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    providerId,
                    modelId,
                    apiKey,
                    baseUrl,
                    customProviderName: isCustomProvider ? customProviderName : "",
                    providerKind: isMediaPurpose ? "media_generation" : (provider?.providerKind || "chat"),
                    mediaModality: isMediaPurpose ? catalogPurposeConfig.modality : (provider?.mediaModality || ""),
                    apiStandard: isCustomProvider
                        ? (customAnthropicRuntime ? "anthropic" : catalogPurpose === "workflow" ? "comfyui" : "openai")
                        : selectedCatalogRuntime.apiStandard,
                    modelType: getModelTypeForPurpose(catalogPurpose),
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.ok === false) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.catalog.connectFailed"),
                    description: extractErrorText(data.detail || data.error, t("app.admin.dashboard.model.hub.catalog.writeFailed")),
                });
                return;
            }
            toast({ title: t("app.admin.dashboard.model.hub.catalog.connected"), description: `${provider?.name || customProviderName || providerId} · ${modelId}` });
            if (isCustomProvider) {
                setSelectedCatalogProviderId(data.providerId || providerId);
            }
            await fetchData();
        }
        finally {
            setIsCatalogBusy(false);
        }
    };
    const handleDeleteCustomCatalogProvider = async (providerId: string) => {
        if (!providerId) return;
        const response = await fetch(`/api/models/providers/custom/${encodeURIComponent(providerId)}`, { method: "DELETE" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.page.kb2803a72"),
                description: extractErrorText(data.detail || data.error, t("app.admin.dashboard.model.hub.catalog.deleteCustomFailed")),
            });
            return;
        }
        setSelectedCatalogProviderId(apiCatalogProviders[0]?.id || "__custom__");
        setCatalogProbeModels([]);
        setSelectedCatalogModelId("");
        setProbedCatalogProviderId("");
        await fetchData();
    };
    const handleTestConnection = async (modelRef: string) => {
        setConnectionStatusMap((current) => ({
            ...current,
            [modelRef]: { status: "testing", message: t("app.admin.dashboard.model.hub.page.kdb5dbeb0") },
        }));
        try {
            const target = models.find((item) => (item.modelRef || item.id) === modelRef);
            const response = await fetch("/api/models/test-connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ modelRef, modelId: target?.modelId, providerId: target?.providerId }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const errorMessage = extractErrorText(data?.detail, "")
                    || extractErrorText(data?.error, "")
                    || t("app.admin.dashboard.model.hub.page.k74ddeaa0");
                setConnectionStatusMap((current) => ({
                    ...current,
                    [modelRef]: { status: "error", message: errorMessage },
                }));
                return;
            }
            const providerPreset = typeof data?.providerPreset === "string" && data.providerPreset
                ? String(data.providerPreset).toUpperCase()
                : "";
            const resolvedEndpoint = typeof data?.resolvedEndpoint === "string" ? data.resolvedEndpoint.replace(/^https?:\/\//, "") : "";
            const capabilityChecks = data?.capabilityChecks && typeof data.capabilityChecks === "object" ? Object.values(data.capabilityChecks) : [];
            const skippedCapabilities = capabilityChecks.filter((item) => (item as { status?: string })?.status === "skipped").length;
            const successMessage = [
                `${data.providerName || "Provider"}${providerPreset ? `/${providerPreset}` : ""} · ${Math.round(Number(data.latencyMs || 0))}ms`,
                resolvedEndpoint,
                skippedCapabilities ? t("app.admin.dashboard.model.hub.catalog.messages.skippedCapabilities") : "",
                data.message || "",
            ].filter(Boolean).join(" · ");
            setConnectionStatusMap((current) => ({
                ...current,
                [modelRef]: { status: "success", message: successMessage },
            }));
        }
        catch (error) {
            const errorMessage = error instanceof Error ? error.message : t("app.admin.dashboard.model.hub.page.k52d13953");
            setConnectionStatusMap((current) => ({
                ...current,
                [modelRef]: { status: "error", message: errorMessage },
            }));
        }
    };
    return (<AdminPageShell>
            <AdminPageHeader title={t("app.admin.dashboard.model.hub.page.kf88eff69")} description={t("app.admin.dashboard.model.hub.page.k45bea0e7")} actions={<>
                        <Button variant="outline" onClick={() => void fetchData()} disabled={isLoading}>
                            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`}/>
                            {t("app.admin.dashboard.model.hub.page.k876e8c06")}
                        </Button>
                        <Button onClick={() => {
                setEditingProvider(null);
                setProviderType("API");
                setProviderCredentialMode("apiKey");
                setProviderApiStandard("openai");
                setProviderBaseUrl("");
                setProviderApiKey("");
                setProviderOauthPath("");
                setPlatformLoginPreset("geminiCli");
                setLocalBackendPreset("ollama");
                setIsProviderDialogOpen(true);
            }}>
                            <Plus className="mr-2 h-4 w-4"/>
                            {t("app.admin.dashboard.model.hub.page.k9e31d9ed")}
                        </Button>
                        <Button disabled={providers.length === 0} onClick={() => { setEditingModel(null); setModelType("TEXT"); setModelProviderId(providers[0]?.id || ""); setRerankApiFlavor("generic"); setIsModelDialogOpen(true); }}>
                            <Plus className="mr-2 h-4 w-4"/>
                            {t("app.admin.dashboard.model.hub.page.k82b1063c")}
                        </Button>
                    </>}/>

            <DomainSummaryStrip items={[
            { label: t("app.admin.dashboard.model.hub.page.ked94504f"), value: hubEnvelope?.data.summary?.providers || providers.length, description: t("app.admin.dashboard.model.hub.page.k3b2008ff") },
            { label: t("app.admin.dashboard.model.hub.page.k0a290add"), value: hubEnvelope?.data.summary?.enabledProviders || providers.filter((item) => item.isEnabled).length, description: t("app.admin.dashboard.model.hub.page.ka6812613") },
            { label: t("app.admin.dashboard.model.hub.page.kc1128e3d"), value: hubEnvelope?.data.summary?.models || models.length, description: t("app.admin.dashboard.model.hub.page.k9196d416") },
            { label: t("app.admin.dashboard.model.hub.page.kf7cda39f"), value: hubEnvelope?.data.config?.governance?.enabled ? t("app.admin.dashboard.model.hub.page.kd945d5d0") : t("app.admin.dashboard.model.hub.page.k12b31ba6"), description: t("app.admin.dashboard.model.hub.page.k3a277197") },
        ]}/>

            <ConfigCard title={t("app.admin.dashboard.model.hub.catalog.title")} description={t("app.admin.dashboard.model.hub.catalog.description")} variant="list" allowOverflow>
                <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
                    <div className="grid gap-3 md:grid-cols-2">
                        {oauthCatalogProviders.map((provider) => {
                            const firstModel = provider.models?.[0];
                            return (
                                <div key={provider.id} className="relative flex h-[158px] flex-col rounded-2xl border bg-card p-4">
                                    <div className="flex items-center justify-between gap-2">
                                        <div className="truncate text-sm font-semibold" title={provider.name}>{provider.name}</div>
                                        <span className="group/info relative shrink-0">
                                            <Badge variant="secondary" className="h-5 px-2 text-[10px]">OAuth</Badge>
                                            <span className="pointer-events-none absolute right-0 top-7 z-30 w-72 rounded-xl bg-slate-950 p-3 text-left text-[11px] leading-5 text-white opacity-0 shadow-2xl transition-opacity group-hover/info:opacity-100">
                                                <span className="block truncate">{provider.name}</span>
                                                <span className="block truncate">Credential: live OAuth</span>
                                                {provider.auth?.path ? <span className="block truncate">Path: {provider.auth.path}</span> : null}
                                                <span className="block truncate">Models: {(provider.models || []).map((model) => model.id).join(", ")}</span>
                                            </span>
                                        </span>
                                    </div>
                                    <Select
                                        defaultValue={firstModel?.id || ""}
                                        onValueChange={(value) => {
                                            const select = document.getElementById(`oauth-model-${provider.id}`) as HTMLInputElement | null;
                                            if (select) select.value = value;
                                        }}
                                    >
                                        <SelectTrigger className="mt-3 h-10">
                                            <SelectValue placeholder={t("app.admin.dashboard.model.hub.catalog.selectModel")}/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {(provider.models || []).map((model) => (
                                                <SelectItem key={`${provider.id}:${model.id}`} value={model.id}>{model.id}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <input id={`oauth-model-${provider.id}`} type="hidden" defaultValue={firstModel?.id || ""}/>
                                    <Button
                                        className="mt-auto w-full"
                                        disabled={isCatalogBusy || !firstModel}
                                        onClick={() => {
                                            const select = document.getElementById(`oauth-model-${provider.id}`) as HTMLInputElement | null;
                                            void handleConnectCatalogModel(provider.id, select?.value || firstModel?.id || "");
                                        }}
                                    >
                                        {t("app.admin.dashboard.model.hub.catalog.detectAndConnect")}
                                    </Button>
                                </div>
                            );
                        })}
                    </div>
                    <div className="rounded-2xl border bg-card p-4">
                        <div className="text-sm font-semibold">{t("app.admin.dashboard.model.hub.catalog.apiProvider")}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.model.hub.catalog.apiProviderPurposeHint")}</div>
                        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                            {CATALOG_PURPOSES.map((purpose) => (
                                <button
                                    key={purpose.id}
                                    type="button"
                                    className={`rounded-xl border px-3 py-2 text-left transition ${catalogPurpose === purpose.id ? "border-slate-900 bg-slate-900 text-white" : "bg-background hover:bg-muted"}`}
                                    onClick={() => {
                                        setCatalogPurpose(purpose.id);
                                        setCatalogProbeModels([]);
                                        setSelectedCatalogModelId("");
                                        setCatalogModelFilter("");
                                        setProbedCatalogProviderId("");
                                        setCatalogProbeStatus(null);
                                        setManualModelEntryEnabled(false);
                                        setCatalogRuntimeProtocol("default");
                                    }}
                                >
                                    <span className="block text-sm font-semibold">{t(purpose.labelKey)}</span>
                                    <span className={`mt-1 block truncate text-[11px] ${catalogPurpose === purpose.id ? "text-white/75" : "text-muted-foreground"}`}>{t(purpose.hintKey)}</span>
                                </button>
                            ))}
                        </div>
                        <div className="mt-3 grid gap-3 md:grid-cols-[1fr_1.2fr_auto]">
                            <Select value={selectedCatalogProviderId} onValueChange={(value) => {
                                setSelectedCatalogProviderId(value);
                                setCatalogProbeModels([]);
                                setSelectedCatalogModelId("");
                                setCatalogModelFilter("");
                                setProbedCatalogProviderId("");
                                setCatalogProbeStatus(null);
                                setManualModelEntryEnabled(false);
                                setCatalogRuntimeProtocol("default");
                            }}>
                                <SelectTrigger>
                                    <SelectValue placeholder={t("app.admin.dashboard.model.hub.catalog.selectProvider")}/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="__custom__">{t("app.admin.dashboard.model.hub.catalog.addCustomProvider")}</SelectItem>
                                    {apiCatalogProviders.filter((item) => item.isCustom).map((provider) => (
                                        <SelectItem key={provider.id} value={provider.id}>
                                            <ProviderOptionLabel provider={provider} suffix={t("app.admin.dashboard.model.hub.catalog.customSuffix")} />
                                        </SelectItem>
                                    ))}
                                    {apiCatalogProviders.filter((item) => !item.isCustom).map((provider) => (
                                        <SelectItem key={provider.id} value={provider.id}>
                                            <ProviderOptionLabel provider={provider} />
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <div className="flex min-w-0 items-center rounded-xl border border-input bg-background">
                                {selectedCredentialHelpUrl ? (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="ml-1 h-9 w-9 shrink-0"
                                        title={selectedCatalogProvider?.credentialHelp?.label || t("app.admin.dashboard.model.hub.catalog.getApiKey")}
                                        onClick={() => window.open(selectedCredentialHelpUrl, "_blank", "noopener,noreferrer")}
                                    >
                                        <ExternalLink className="h-4 w-4"/>
                                    </Button>
                                ) : null}
                                <Input value={catalogApiKey} onChange={(event) => setCatalogApiKey(event.target.value)} type="password" className="min-w-0 border-0 shadow-none focus-visible:ring-0" placeholder={t("app.admin.dashboard.model.hub.catalog.apiKeyPlaceholder")}/>
                            </div>
                            <Button disabled={isCatalogBusy} onClick={() => void handleProbeCatalogProvider()}>{t("app.admin.dashboard.model.hub.catalog.probe")}</Button>
                        </div>
                        <div className="mt-2 text-xs text-muted-foreground">
                            {t("app.admin.dashboard.model.hub.catalog.currentPurpose", { purpose: catalogPurposeLabel, hint: catalogPurposeHint })}
                        </div>
                        {catalogPurpose === "chat" && selectedCatalogProvider?.anthropicCompatible?.baseUrl ? (
                            <div className="mt-3 grid gap-2 rounded-xl border border-dashed px-3 py-2">
                                <Label className="text-xs font-semibold">{t("app.admin.dashboard.model.hub.catalog.runtimeProtocol")}</Label>
                                <Select value={catalogRuntimeProtocol} onValueChange={(value: CatalogRuntimeProtocol) => setCatalogRuntimeProtocol(value)}>
                                    <SelectTrigger className="h-9">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="default">{t("app.admin.dashboard.model.hub.catalog.runtimeProtocolDefault")}</SelectItem>
                                        <SelectItem value="anthropic">{t("app.admin.dashboard.model.hub.catalog.runtimeProtocolAnthropic")}</SelectItem>
                                    </SelectContent>
                                </Select>
                                <div className="text-xs text-muted-foreground">
                                    {catalogRuntimeProtocol === "anthropic"
                                        ? t("app.admin.dashboard.model.hub.catalog.runtimeProtocolAnthropicHint", { baseUrl: selectedCatalogProvider.anthropicCompatible.baseUrl })
                                        : t("app.admin.dashboard.model.hub.catalog.runtimeProtocolProbeHint", { url: previewModelsUrl(selectedCatalogProvider) })}
                                </div>
                            </div>
                        ) : null}
                        {selectedCatalogProviderId === "__custom__" ? (
                            <div className="mt-3 grid gap-3 md:grid-cols-2">
                                <Input value={customProviderName} onChange={(event) => setCustomProviderName(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.catalog.customNamePlaceholder")}/>
                                <Input value={customProviderBaseUrl} onChange={(event) => setCustomProviderBaseUrl(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.catalog.customBaseUrlPlaceholder")}/>
                                <div className="md:col-span-2 rounded-xl border border-dashed px-3 py-2 text-xs text-muted-foreground">
                                    {catalogPurpose === "chat"
                                        ? t("app.admin.dashboard.model.hub.catalog.chatCustomHint")
                                        : t("app.admin.dashboard.model.hub.catalog.mediaCustomHint", { purpose: catalogPurposeLabel })}
                                </div>
                            </div>
                        ) : selectedCatalogProvider?.isCustom ? (
                            <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-dashed px-3 py-2 text-xs text-muted-foreground">
                                <span>{selectedCatalogProvider.baseUrl}</span>
                                <Button variant="outline" size="sm" onClick={() => void handleDeleteCustomCatalogProvider(selectedCatalogProvider.id)}>
                                    <X className="mr-1 h-3 w-3"/>{t("app.admin.dashboard.model.hub.catalog.deleteCustom")}
                                </Button>
                            </div>
                        ) : selectedCatalogProvider ? (
                            <div className="mt-3 rounded-xl border border-dashed px-3 py-2 text-xs text-muted-foreground">
                                {selectedCatalogProvider.probeStrategy === "catalog_only"
                                    ? t("app.admin.dashboard.model.hub.catalog.presetBaseUrl", { baseUrl: selectedCatalogProvider.baseUrl || t("app.admin.dashboard.model.hub.catalog.providerDefault"), help: selectedCatalogProvider.credentialHelp?.label || t("app.admin.dashboard.model.hub.catalog.openProvider") })
                                    : t("app.admin.dashboard.model.hub.catalog.probeUrl", { url: previewModelsUrl(selectedCatalogProvider) })}
                                {selectedCatalogProvider.anthropicCompatible?.baseUrl ? (
                                    <div className="mt-1">
                                        {t("app.admin.dashboard.model.hub.catalog.anthropicCompatibleHint", { baseUrl: selectedCatalogProvider.anthropicCompatible.baseUrl })}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                        {catalogProbeStatus ? (
                            <div className={`mt-3 rounded-xl border px-3 py-2 text-xs ${catalogProbeStatus.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                                <div>{catalogProbeStatus.message}</div>
                                {catalogProbeStatus.resolvedModelsUrl ? <div className="mt-1 opacity-80">URL: {catalogProbeStatus.resolvedModelsUrl}</div> : null}
                                {catalogProbeStatus.usedStoredCredential ? <div className="mt-1 opacity-80">{t("app.admin.dashboard.model.hub.catalog.usedStoredCredential")}</div> : null}
                            </div>
                        ) : null}
                        {(catalogProbeModels.length > 0 || manualModelEntryEnabled) && (
                            <div className="mt-3 space-y-3">
                                <Input
                                    value={catalogModelFilter}
                                    onChange={(event) => {
                                        setCatalogModelFilter(event.target.value);
                                        if (manualModelEntryEnabled) {
                                            setSelectedCatalogModelId(event.target.value.trim());
                                        }
                                    }}
                                    placeholder={manualModelEntryEnabled ? t("app.admin.dashboard.model.hub.catalog.manualModelPlaceholder") : t("app.admin.dashboard.model.hub.catalog.modelFilterPlaceholder")}
                                />
                                {!manualModelEntryEnabled ? (
                                    <div className="max-h-64 overflow-y-auto rounded-xl border bg-background p-2">
                                        {visibleCatalogModels.map((model) => {
                                            const modelId = model.modelId || model.id;
                                            const modelIcon = resolveModelIcon({
                                                modelId,
                                                providerId: probedCatalogProviderId || selectedCatalogProvider?.id || selectedCatalogProviderId,
                                                providerName: selectedCatalogProvider?.name || "",
                                                explicitAsset: model.logoAsset || null,
                                            });
                                            return (
                                                <button
                                                    key={`${probedCatalogProviderId || selectedCatalogProviderId}:${modelId}`}
                                                    type="button"
                                                    className={`mb-1 flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${selectedCatalogModelId === modelId ? "bg-slate-900 text-white" : "hover:bg-muted"}`}
                                                    onClick={() => {
                                                        setSelectedCatalogModelId(modelId);
                                                        setCatalogModelFilter(modelId);
                                                    }}
                                                >
                                                    <span className="flex min-w-0 items-center gap-2">
                                                        <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md ${selectedCatalogModelId === modelId ? "bg-white/10" : "bg-slate-100"}`}>
                                                            {modelIcon ? <img src={modelIcon} alt="" className="h-4 w-4 object-contain" /> : null}
                                                        </span>
                                                        <span className="truncate">{modelId}</span>
                                                    </span>
                                                    {model.contextWindow ? <span className="ml-3 shrink-0 text-xs opacity-70">{model.contextWindow}</span> : null}
                                                </button>
                                            );
                                        })}
                                        {visibleCatalogModels.length === 0 ? (
                                            <div className="px-3 py-4 text-sm text-muted-foreground">{t("app.admin.dashboard.model.hub.catalog.noMatchedModels")}</div>
                                        ) : null}
                                    </div>
                                ) : null}
                                <Button
                                    disabled={isCatalogBusy || !selectedCatalogModelId}
                                    onClick={() => void handleConnectCatalogModel(probedCatalogProviderId || selectedCatalogProviderId, selectedCatalogModelId, catalogApiKey)}
                                >
                                    {t("app.admin.dashboard.model.hub.catalog.connectCurrent")}
                                </Button>
                            </div>
                        )}
                    </div>
                </div>
            </ConfigCard>

            <ConfigCard title={t("app.admin.dashboard.model.hub.page.kd0251a96")} description={t("app.admin.dashboard.model.hub.page.k79d4e8e7")} variant="list" allowOverflow>
                {providers.length === 0 ? (<EmptyState title={t("app.admin.dashboard.model.hub.page.k8d04b4ed")} description={t("app.admin.dashboard.model.hub.page.k9e469730")}/>) : (<div className="grid gap-3 md:grid-cols-3 2xl:grid-cols-5">
                        {providers.map((provider) => (<ProviderCard key={provider.id} provider={provider} health={providerOverviewById.get(provider.code) || providerOverviewById.get(provider.id) || null} onEdit={() => {
                    const inferredPreset = inferPlatformLoginPreset({
                        providerType: provider.type,
                        apiStandard: provider.apiStandard,
                        baseUrl: provider.baseUrl,
                        oauthPath: provider.oauthPath,
                        code: provider.code,
                        name: provider.name,
                    });
                    const inferredLocalPreset = inferLocalBackendPreset({
                        providerType: provider.type,
                        baseUrl: provider.baseUrl,
                        preset: provider.localBackendPreset,
                        code: provider.code,
                        name: provider.name,
                    });
                    setEditingProvider(provider);
                    setProviderType(provider.type || "API");
                    setProviderCredentialMode(provider.type === "PLATFORM" ? "oauthFile" : (provider.credentialMode || "apiKey"));
                    setProviderApiStandard((provider.apiStandard as "openai" | "anthropic" | "gemini" | "comfyui") || "openai");
                    setProviderBaseUrl(provider.baseUrl || "");
                    setProviderApiKey(provider.apiKey || "");
                    setProviderOauthPath(provider.oauthPath || "");
                    setPlatformLoginPreset(inferredPreset);
                    setLocalBackendPreset(inferredLocalPreset);
                    setIsProviderDialogOpen(true);
                }} onDelete={handleDeleteProvider} onToggle={async (id, enabled) => {
                    await fetch(`/api/providers/${id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ isEnabled: enabled }),
                    });
                    await fetchData();
                }}/>))}
                    </div>)}
            </ConfigCard>

            <ConfigCard title={t("app.admin.dashboard.model.hub.page.k6a95644c")} description={t("app.admin.dashboard.model.hub.page.k933aeed1")} variant="list" allowOverflow>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="text-sm text-slate-500">{t("app.admin.dashboard.model.hub.page.kdea3cadf")}</div>
                    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full max-w-5xl">
                        <TabsList className="grid w-full grid-cols-4 rounded-2xl bg-slate-100 md:grid-cols-6 xl:grid-cols-11">
                            <TabsTrigger value="all">{t("app.admin.dashboard.model.hub.page.ke8cc995b")}</TabsTrigger>
                            <TabsTrigger value="text">{t("app.admin.dashboard.model.hub.page.kc4eaa582")}</TabsTrigger>
                            <TabsTrigger value="multimodal">{t("app.admin.dashboard.model.hub.page.k2d2f7b56")}</TabsTrigger>
                            <TabsTrigger value="image">{t("app.admin.dashboard.model.hub.catalog.tabImage")}</TabsTrigger>
                            <TabsTrigger value="video">{t("app.admin.dashboard.model.hub.catalog.tabVideo")}</TabsTrigger>
                            <TabsTrigger value="voice">{t("app.admin.dashboard.model.hub.catalog.tabVoice")}</TabsTrigger>
                            <TabsTrigger value="music">{t("app.admin.dashboard.model.hub.catalog.tabMusic")}</TabsTrigger>
                            <TabsTrigger value="workflow">{t("app.admin.dashboard.model.hub.catalog.tabWorkflow")}</TabsTrigger>
                            <TabsTrigger value="model3d">{t("app.admin.dashboard.model.hub.catalog.tabModel3d")}</TabsTrigger>
                            <TabsTrigger value="embedding">{t("app.admin.dashboard.model.hub.page.kc1798b61")}</TabsTrigger>
                            <TabsTrigger value="rerank">{t("app.admin.dashboard.model.hub.page.k81ac6b74")}</TabsTrigger>
                        </TabsList>
                    </Tabs>
                </div>

                {filteredModels.length === 0 ? (<EmptyState title={t("app.admin.dashboard.model.hub.page.k14457a61")} description={t("app.admin.dashboard.model.hub.page.k8d6baa0f")}/>) : (<div className="grid gap-3 md:grid-cols-3 2xl:grid-cols-5">
                        {filteredModels.map((model) => (<ModelCardV2 key={model.modelRef || model.id} model={model} controlMeta={controlModelsById.get(model.modelRef || model.id) || null} isDefault={(model.modelRef || model.id) === defaultModelRef} connectionStatus={connectionStatusMap[model.modelRef || model.id] || null} onEdit={() => {
                    setEditingModel(model);
                    setModelType(model.type || "TEXT");
                    setModelProviderId(model.providerId);
                    setRerankApiFlavor(model.rerankApiFlavor || "generic");
                    setIsModelDialogOpen(true);
                }} onDelete={handleDeleteModel} onTestConnection={handleTestConnection} onSetDefault={handleSetDefaultModel}/>))}
                    </div>)}
            </ConfigCard>

            {hubEnvelope ? (<SourceMetaRow source={hubEnvelope.source} savePath={hubEnvelope.savePath} reloadRequired={hubEnvelope.reloadRequired}/>) : null}

            <Dialog open={isProviderDialogOpen} onOpenChange={setIsProviderDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{editingProvider ? t("app.admin.dashboard.model.hub.page.k03d9a3c5") : t("app.admin.dashboard.model.hub.page.k9e31d9ed")}</DialogTitle>
                    </DialogHeader>
                    <form key={`${editingProvider?.id || "new"}-${providerType}-${providerCredentialMode}`} onSubmit={handleSaveProvider} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="provider-name">{t("app.admin.dashboard.model.hub.page.kd00c0239")}</Label>
                            <Input id="provider-name" name="name" defaultValue={editingProvider?.name || ""} required/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="provider-code">{t("app.admin.dashboard.model.hub.page.ke46386e9")}</Label>
                            <Input id="provider-code" name="code" defaultValue={editingProvider?.code || ""} required/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="provider-type">{t("app.admin.dashboard.model.hub.page.k8de6f532")}</Label>
                            <input type="hidden" name="type" value={providerType}/>
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
                setProviderApiKey("");
            }
            else if (value === "LOCAL") {
                const config = getLocalBackendPresetConfig(localBackendPreset);
                setProviderCredentialMode("apiKey");
                setProviderApiStandard(config.apiStandard);
                setProviderBaseUrl(config.baseUrl);
                setProviderApiKey(config.apiKey);
                setProviderOauthPath("");
            }
        }}>
                                <SelectTrigger id="provider-type"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="API">API</SelectItem>
                                    <SelectItem value="LOCAL">{t("app.admin.dashboard.model.hub.page.kde244a7f")}</SelectItem>
                                    <SelectItem value="PLATFORM">{t("app.admin.dashboard.model.hub.page.k2093dbe7")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {platformProviderSelected ? (<>
                                <input type="hidden" name="platformLoginPreset" value={platformLoginPreset}/>
                                <div className="space-y-2">
                                    <Label htmlFor="platform-login-preset">{t("app.admin.dashboard.model.hub.page.k1f6f2bda")}</Label>
                                    <Select value={platformLoginPreset} onValueChange={(value: PlatformLoginPreset) => {
                const config = getPlatformLoginPresetConfig(value);
                setPlatformLoginPreset(value);
                setProviderCredentialMode("oauthFile");
                setProviderApiStandard(config.apiStandard);
                setProviderBaseUrl(config.baseUrl);
                setProviderOauthPath(config.oauthPath);
            }}>
                                        <SelectTrigger id="platform-login-preset"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {Object.values(PLATFORM_LOGIN_PRESETS).map((preset) => (<SelectItem key={preset.id} value={preset.id}>
                                                    {preset.label}
                                                </SelectItem>))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">{activePlatformPreset.description}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="provider-api-standard-readonly">{t("app.admin.dashboard.model.hub.page.k3a701154")}</Label>
                                    <Input id="provider-api-standard-readonly" value={providerApiStandard === "openai"
                ? t("app.admin.dashboard.model.hub.page.kdab0f774")
                : providerApiStandard === "anthropic"
                    ? t("app.admin.dashboard.model.hub.page.k504d12c7")
                    : providerApiStandard === "comfyui"
                        ? "ComfyUI"
                        : t("app.admin.dashboard.model.hub.page.k560df989")} readOnly/>
                                    <input type="hidden" name="apiStandard" value={providerApiStandard}/>
                                </div>
                            </>) : providerType === "LOCAL" ? (<>
                                <input type="hidden" name="localBackendPreset" value={localBackendPreset}/>
                                <div className="space-y-2">
                                    <Label htmlFor="provider-local-preset">{t("app.admin.dashboard.model.hub.page.kd683ee7e")}</Label>
                                    <Select value={localBackendPreset} onValueChange={(value: LocalBackendPreset) => {
                const config = getLocalBackendPresetConfig(value);
                setLocalBackendPreset(value);
                setProviderCredentialMode("apiKey");
                setProviderApiStandard(config.apiStandard);
                setProviderBaseUrl(config.baseUrl);
                setProviderApiKey(config.apiKey);
            }}>
                                        <SelectTrigger id="provider-local-preset"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            {Object.values(LOCAL_BACKEND_PRESETS).map((preset) => (<SelectItem key={preset.id} value={preset.id}>
                                                    {preset.label}
                                                </SelectItem>))}
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">{t(localBackendConfig.description)}</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="provider-api-standard-readonly">{t("app.admin.dashboard.model.hub.page.k3a701154")}</Label>
                                    <Input id="provider-api-standard-readonly" value={t("app.admin.dashboard.model.hub.page.kdab0f774")} readOnly/>
                                    <input type="hidden" name="apiStandard" value={providerApiStandard}/>
                                </div>
                            </>) : (<div className="space-y-2">
                                <Label htmlFor="provider-api-standard">{t("app.admin.dashboard.model.hub.page.k3a701154")}</Label>
                                <input type="hidden" name="apiStandard" value={providerApiStandard}/>
                                <Select value={providerApiStandard} onValueChange={(value: "openai" | "anthropic" | "gemini" | "comfyui") => setProviderApiStandard(value)}>
                                    <SelectTrigger id="provider-api-standard"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="openai">{t("app.admin.dashboard.model.hub.page.kdab0f774")}</SelectItem>
                                        <SelectItem value="anthropic">{t("app.admin.dashboard.model.hub.page.k504d12c7")}</SelectItem>
                                        <SelectItem value="gemini">{t("app.admin.dashboard.model.hub.page.k560df989")}</SelectItem>
                                        <SelectItem value="comfyui">ComfyUI</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>)}
                        <div className="space-y-2">
                            <Label htmlFor="provider-base-url">{t("app.admin.dashboard.model.hub.page.k8331921c")}</Label>
                            <Input
                                id="provider-base-url"
                                name="baseUrl"
                                value={providerBaseUrl}
                                onChange={(event) => {
                                    const nextValue = event.target.value;
                                    setProviderBaseUrl(nextValue);
                                    if (isXiaomiAnthropicBaseUrl(nextValue) && providerApiStandard !== "anthropic") {
                                        setProviderApiStandard("anthropic");
                                    }
                                }}
                            />
                            {isXiaomiAnthropicBaseUrl(providerBaseUrl) ? (
                                <p className="text-xs text-amber-600">
                                    {t("app.admin.dashboard.model.hub.catalog.manualAnthropicBaseUrlHint")}
                                </p>
                            ) : null}
                        </div>
                        {!platformProviderSelected ? (<div className="space-y-2">
                                <Label htmlFor="provider-credential-mode">{t("app.admin.dashboard.model.hub.page.k1947a36f")}</Label>
                                <input type="hidden" name="credentialMode" value={providerCredentialMode}/>
                                <Select value={providerCredentialMode} onValueChange={(value: "apiKey" | "oauthFile") => setProviderCredentialMode(value)}>
                                    <SelectTrigger id="provider-credential-mode"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="apiKey">API Key</SelectItem>
                                        <SelectItem value="oauthFile">{t("app.admin.dashboard.model.hub.page.ke507bb9a")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>) : (<input type="hidden" name="credentialMode" value="oauthFile"/>)}
                        {platformProviderSelected || providerCredentialMode === "oauthFile" ? (<div className="space-y-2">
                                <Label htmlFor="provider-oauth-path">{t("app.admin.dashboard.model.hub.page.k686313b2")}</Label>
                                <div className="flex items-center rounded-xl border border-input bg-background">
                                    <span className="shrink-0 border-r border-border/60 px-3 text-sm text-muted-foreground">oauth:</span>
                                        <Input id="provider-oauth-path" name="oauthPath" className="border-0 shadow-none focus-visible:ring-0" value={providerOauthPath} onChange={(event) => setProviderOauthPath(event.target.value)} placeholder={activePlatformPreset.oauthPath}/>
                                    </div>
                                <p className={`text-xs ${(platformProviderSelected ? activePlatformPreset.supportState === "preset-only" : providerApiStandard === "gemini") ? "text-amber-600" : "text-muted-foreground"}`}>{oauthHint}</p>
                            </div>) : (<div className="space-y-2">
                                <Label htmlFor="provider-api-key">API Key</Label>
                                <Input id="provider-api-key" name="apiKey" type="password" value={providerApiKey} onChange={(event) => setProviderApiKey(event.target.value)} placeholder={providerType === "LOCAL" ? localBackendConfig.apiKey : ""}/>
                                {providerType === "LOCAL" ? (<p className="text-xs text-muted-foreground">{t(localBackendConfig.helpText)}</p>) : null}
                            </div>)}
                        <Button type="submit" className="w-full">{t("app.admin.dashboard.model.hub.page.k93b84c67")}</Button>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog open={isModelDialogOpen} onOpenChange={setIsModelDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{editingModel ? t("app.admin.dashboard.model.hub.page.k37053cf7") : t("app.admin.dashboard.model.hub.page.k82b1063c")}</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleSaveModel} className="space-y-4">
                        {providers.length === 0 ? (<EmptyState title={t("app.admin.dashboard.model.hub.page.k5ca95d1d")} description={t("app.admin.dashboard.model.hub.page.k4119e026")}/>) : null}
                        <div className="space-y-2">
                            <Label htmlFor="model-provider">{t("app.admin.dashboard.model.hub.page.kc9371614")}</Label>
                            <input type="hidden" name="providerId" value={modelProviderId}/>
                            <Select value={modelProviderId} onValueChange={setModelProviderId}>
                                <SelectTrigger id="model-provider"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {providers.map((provider) => (<SelectItem key={provider.id} value={provider.id}>{provider.name}</SelectItem>))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-model-id">{t("app.admin.dashboard.model.hub.page.k8dbca6d6")}</Label>
                            <Input id="model-model-id" name="modelId" defaultValue={editingModel?.modelId || ""} required/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-type">{t("app.admin.dashboard.model.hub.page.k0bce4283")}</Label>
                            <input type="hidden" name="type" value={modelType}/>
                            <Select value={modelType} onValueChange={setModelType}>
                                <SelectTrigger id="model-type"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="TEXT">{t("app.admin.dashboard.model.hub.page.kc4eaa582")}</SelectItem>
                                    <SelectItem value="MULTIMODAL">{t("app.admin.dashboard.model.hub.page.k2d2f7b56")}</SelectItem>
                                    <SelectItem value="IMAGE">{t("app.admin.dashboard.model.hub.catalog.typeImage")}</SelectItem>
                                    <SelectItem value="VIDEO">{t("app.admin.dashboard.model.hub.catalog.typeVideo")}</SelectItem>
                                    <SelectItem value="VOICE">{t("app.admin.dashboard.model.hub.catalog.typeVoice")}</SelectItem>
                                    <SelectItem value="MUSIC">{t("app.admin.dashboard.model.hub.catalog.typeMusic")}</SelectItem>
                                    <SelectItem value="WORKFLOW">{t("app.admin.dashboard.model.hub.catalog.typeWorkflow")}</SelectItem>
                                    <SelectItem value="MODEL3D">{t("app.admin.dashboard.model.hub.catalog.typeModel3d")}</SelectItem>
                                    <SelectItem value="MEDIA">{t("app.admin.dashboard.model.hub.catalog.typeMediaCompat")}</SelectItem>
                                    <SelectItem value="EMBEDDING">{t("app.admin.dashboard.model.hub.page.kc1798b61")}</SelectItem>
                                    <SelectItem value="RERANK">{t("app.admin.dashboard.model.hub.page.k81ac6b74")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {modelType === "RERANK" ? (<div className="space-y-2">
                                <Label htmlFor="model-rerank-flavor">{t("app.admin.dashboard.model.hub.page.k51b60583")}</Label>
                                <input type="hidden" name="rerankApiFlavor" value={rerankApiFlavor}/>
                                <Select value={rerankApiFlavor} onValueChange={setRerankApiFlavor}>
                                    <SelectTrigger id="model-rerank-flavor"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="generic">{t("app.admin.dashboard.model.hub.page.k2b007f7f")}</SelectItem>
                                        <SelectItem value="vllm">{t("app.admin.dashboard.model.hub.page.k83238674")}</SelectItem>
                                        <SelectItem value="nexa">{t("app.admin.dashboard.model.hub.page.ke12df81e")}</SelectItem>
                                    </SelectContent>
                                </Select>
                                <p className="text-xs text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.page.kfaf657c9")}
                                </p>
                            </div>) : null}
                        {modelType === "TEXT" || modelType === "MULTIMODAL" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="model-context-window">{t("app.admin.dashboard.model.hub.page.k20e21cd2")}</Label>
                                    <Input id="model-context-window" name="contextWindow" type="number" defaultValue={editingModel?.contextWindow ?? ""} placeholder={t("app.admin.dashboard.model.hub.page.contextWindowPlaceholder")}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="model-max-tokens">{t("app.admin.dashboard.model.hub.page.k1f9a045b")}</Label>
                                    <Input id="model-max-tokens" name="maxTokens" type="number" defaultValue={editingModel?.maxTokens ?? ""} placeholder={t("app.admin.dashboard.model.hub.page.maxTokensPlaceholder")}/>
                                </div>
                            </div>
                        ) : MEDIA_MODEL_TYPES.has(modelType) ? (
                            <div className="rounded-xl border bg-muted/30 p-3 text-sm text-muted-foreground">
                                {t("app.admin.dashboard.model.hub.catalog.mediaModelNotice")}
                            </div>
                        ) : null}
                        <Button type="submit" className="w-full">{t("app.admin.dashboard.model.hub.page.kb7dfaded")}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </AdminPageShell>);
}
