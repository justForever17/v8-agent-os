"use client";
import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, Mic, Plus, RefreshCw, Save, Trash2, Upload, Volume2, X } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
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
import { HydrationSafeClientOnly } from "@/components/ui/hydration-safe-client-only";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import type { ConfigRegistryEnvelope } from "@/lib/config-registry";
import { getAdminOptions, resolveAdminLabel } from "@/lib/admin-labels";
import audioVoicePresets from "@/lib/models/audio-voice-presets.json";
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
    voiceAppId?: string | null;
    voiceResourceId?: string | null;
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
    thinkingControl?: Record<string, unknown> | null;
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
    status: "idle" | "testing" | "success" | "warning" | "error";
    message?: string;
};
type ModelReasoningRepairStatus = {
    status: "idle" | "repairing" | "success" | "warning" | "error";
    message?: string;
};
type CatalogModel = {
    id: string;
    modelId?: string;
    type?: string;
    contextWindow?: number | null;
    maxTokens?: number | null;
    logoAsset?: string | null;
    capabilities?: Record<string, boolean> | string[];
    mediaLimits?: Record<string, unknown>;
    operationKinds?: string[];
    sourceProviderId?: string;
    sourceProviderName?: string;
};
type CatalogProvider = {
    id: string;
    name: string;
    apiStandard?: string;
    providerKind?: string;
    type?: string;
    catalogVisibility?: string;
    mediaModality?: string;
    adapter?: string;
    baseUrl?: string;
    modelsUrl?: string;
    modelsPath?: string;
    request?: { submitPath?: string };
    capabilityEntries?: CatalogProvider[];
    sourceProviderId?: string;
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
type CatalogRuntimeProtocol = "default" | "anthropic";
type AudioRuntimeConfig = {
    stt: {
        active_provider: string;
        providers: {
            custom: {
                endpoint?: string;
                api_key?: string;
                protocol?: string;
                model?: string;
                language?: string;
                fileField?: string;
                responseTextPath?: string;
                headers?: string | Record<string, string>;
            };
            baidu: { app_id?: string; api_key?: string; secret_key?: string };
            volcengine?: { app_id?: string; access_token?: string; cluster?: string };
        };
        model_ref?: { modelRef?: string; mode?: string; language?: string; prompt?: string };
    };
    tts: {
        active_provider: string;
        edge_tts: { voice?: string; rate?: string; volume?: string };
        custom: {
            endpoint?: string;
            api_key?: string;
            voice?: string;
            protocol?: string;
            model?: string;
            format?: string;
            speed?: string;
            responseAudioPath?: string;
            headers?: string | Record<string, string>;
        };
        model_ref?: { modelRef?: string; voice?: string; format?: string; speed?: string };
    };
};
type AudioVoicePreset = {
    value: string;
    label?: string;
    labelKey?: string;
};
type AudioVoiceOption = {
    value: string;
    label: string;
    group?: string;
    deletable?: boolean;
    source?: "remote" | "preset" | "local_ledger" | string;
};
type TtsVoiceCapabilities = {
    supportsVoiceManager?: boolean;
    supportsList?: boolean;
    supportsDelete?: boolean;
    supportsCloneUpload?: boolean;
};
type TtsVoiceProviderInfo = {
    provider?: string;
    capabilities?: TtsVoiceCapabilities;
};
type AudioVoicePresetProvider = {
    match?: string[];
    protocol?: string;
    defaultEndpoint?: string;
    voices?: AudioVoicePreset[];
    supportsRemoteVoiceList?: boolean;
    remoteVoiceListPath?: string;
};
type AudioVoicePresetTable = {
    providers?: Record<string, AudioVoicePresetProvider>;
};

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
const RETRIEVAL_MODEL_TYPES = new Set<string>(["EMBEDDING", "RERANK", "RERANKER"]);
const TTS_MODEL_TYPES = new Set<string>(["AUDIO", "VOICE"]);
const DEFAULT_AUDIO_CONFIG: AudioRuntimeConfig = {
    stt: {
        active_provider: "baidu",
        providers: {
            custom: { endpoint: "", api_key: "", protocol: "multipart", model: "", language: "zh-CN", fileField: "file", responseTextPath: "text", headers: "" },
            baidu: { app_id: "", api_key: "", secret_key: "" },
            volcengine: { app_id: "", access_token: "", cluster: "" },
        },
        model_ref: { modelRef: "", mode: "audio_input", language: "zh-CN", prompt: "" },
    },
    tts: {
        active_provider: "edge-tts",
        edge_tts: { voice: "zh-CN-XiaoxiaoNeural", rate: "+0%", volume: "+0%" },
        custom: { endpoint: "", api_key: "", voice: "", protocol: "json_audio_stream", model: "", format: "mp3", speed: "", responseAudioPath: "", headers: "" },
        model_ref: { modelRef: "", voice: "", format: "mp3", speed: "" },
    },
};
const AUDIO_VOICE_PRESET_TABLE = audioVoicePresets as AudioVoicePresetTable;

function localizeAudioVoicePresets(key: string, t: (key: string) => string): { value: string; label: string }[] {
    const provider = AUDIO_VOICE_PRESET_TABLE.providers?.[key];
    return (provider?.voices || []).map((voice) => ({
        value: voice.value,
        label: voice.labelKey ? t(voice.labelKey) : voice.label || voice.value,
    }));
}

function resolveAudioVoicePresetKey(modelRef: string): string {
    const normalized = modelRef.toLowerCase();
    for (const [key, provider] of Object.entries(AUDIO_VOICE_PRESET_TABLE.providers || {})) {
        if (key === "edge-tts") continue;
        if ((provider.match || []).some((pattern) => normalized.includes(pattern.toLowerCase()))) {
            return key;
        }
    }
    return "";
}

function voicePresetsForCustomTtsProtocol(protocol: string, t: (key: string) => string): { value: string; label: string }[] {
    if (protocol === "openai_speech") return localizeAudioVoicePresets("openai", t);
    if (protocol === "minimax_t2a_v2") return localizeAudioVoicePresets("minimax", t);
    return [];
}

function headerValueForInput(value: string | Record<string, string> | undefined): string {
    if (!value) return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value);
}

function sttEndpointPlaceholder(protocol: string): string {
    if (protocol === "openai_transcription") return "https://api.openai.com/v1/audio/transcriptions";
    if (protocol === "json_base64") return "https://example.com/transcribe-json";
    return "https://example.com/transcribe";
}

function ttsEndpointPlaceholder(protocol: string): string {
    if (protocol === "openai_speech") return "https://api.openai.com/v1/audio/speech";
    if (protocol === "minimax_t2a_v2") return "https://api.minimaxi.com/v1/t2a_v2";
    return "https://example.com/tts";
}

function mergeAudioConfig(value: unknown): AudioRuntimeConfig {
    const incoming = value && typeof value === "object" ? value as Partial<AudioRuntimeConfig> : {};
    const stt: Partial<AudioRuntimeConfig["stt"]> = incoming.stt || {};
    const tts: Partial<AudioRuntimeConfig["tts"]> = incoming.tts || {};
    return {
        stt: {
            ...DEFAULT_AUDIO_CONFIG.stt,
            ...stt,
            providers: {
                ...DEFAULT_AUDIO_CONFIG.stt.providers,
                ...(stt.providers || {}),
            },
            model_ref: {
                ...DEFAULT_AUDIO_CONFIG.stt.model_ref,
                ...(stt.model_ref || {}),
            },
        },
        tts: {
            ...DEFAULT_AUDIO_CONFIG.tts,
            ...tts,
            edge_tts: {
                ...DEFAULT_AUDIO_CONFIG.tts.edge_tts,
                ...(tts.edge_tts || {}),
            },
            custom: {
                ...DEFAULT_AUDIO_CONFIG.tts.custom,
                ...(tts.custom || {}),
            },
            model_ref: {
                ...DEFAULT_AUDIO_CONFIG.tts.model_ref,
                ...(tts.model_ref || {}),
            },
        },
    };
}

function modelRefFor(model: AIModel): string {
    return model.modelRef || `${model.providerId}::${model.modelId || model.id}`;
}

function modelLabel(model: AIModel): string {
    return `${model.provider?.name || model.providerId} · ${model.modelId || model.id}`;
}

function hasAudioInputCapability(model: AIModel): boolean {
    const ref = `${modelRefFor(model)} ${model.modelId || ""}`.toLowerCase();
    return ref.includes("stt")
        || ref.includes("asr")
        || ref.includes("whisper")
        || ref.includes("transcribe")
        || ref.includes("speech-to-text")
        || ref.includes("live-audio");
}

function hasAudioOutputCapability(model: AIModel, controlMeta?: ControlPlaneModel | null): boolean {
    const type = normalizeModelType(model.type || controlMeta?.type);
    const capabilities = controlMeta?.capabilities;
    const ref = `${modelRefFor(model)} ${model.modelId || ""}`.toLowerCase();
    return TTS_MODEL_TYPES.has(type) || Boolean(capabilities?.audio || capabilities?.voice) || ref.includes("tts") || ref.includes("live-audio");
}

function isManagedModelRefTtsVoiceModel(model: AIModel | null | undefined, modelRef: string): boolean {
    if (!model && !modelRef) return false;
    const type = normalizeModelType(model?.type);
    const probe = `${modelRef} ${model?.providerId || ""} ${model?.provider?.name || ""} ${model?.modelId || ""}`.toLowerCase();
    const looksLikeTts = type === "VOICE" || type === "AUDIO" || probe.includes("tts") || probe.includes("speech") || probe.includes("voice");
    return looksLikeTts && (
        probe.includes("minimax")
        || probe.includes("cosyvoice")
        || probe.includes("dashscope")
        || probe.includes("bailian")
        || probe.includes("volcengine")
        || probe.includes("doubao-voice")
    );
}

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

function urlPath(value: string | undefined) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {
        const parsed = new URL(raw);
        return parsed.pathname.replace(/\/$/, "");
    } catch {
        const path = raw.startsWith("/") ? raw : `/${raw}`;
        return path.replace(/\/$/, "");
    }
}

function mediaRelativeSubmitPath(rootBaseUrl: string | undefined, sourceBaseUrl: string | undefined, submitPath: string | undefined) {
    const submit = urlPath(submitPath);
    if (!submit) return "";
    const sourceBase = urlPath(sourceBaseUrl);
    const rootBase = urlPath(rootBaseUrl);
    const fullPath = sourceBase && !submit.startsWith(`${sourceBase}/`) && submit !== sourceBase
        ? `${sourceBase}/${submit.replace(/^\//, "")}`
        : submit;
    if (rootBase && (fullPath === rootBase || fullPath.startsWith(`${rootBase}/`))) {
        return fullPath.slice(rootBase.length).replace(/^\//, "");
    }
    return submit.replace(/^\//, "");
}

function endpointMediaCatalogModel(model: CatalogModel, sourceProvider: CatalogProvider, rootProvider: CatalogProvider): CatalogModel {
    const providerModelId = model.modelId || model.id;
    const relativePath = mediaRelativeSubmitPath(rootProvider.baseUrl, sourceProvider.baseUrl, sourceProvider.request?.submitPath);
    const displayModelId = relativePath && providerModelId ? `${relativePath}/${providerModelId}` : providerModelId;
    return {
        ...model,
        id: displayModelId,
        modelId: displayModelId,
        mediaLimits: {
            ...(model.mediaLimits || {}),
            adapterProviderId: sourceProvider.id,
            providerModelId,
            displayModelId,
        },
        sourceProviderId: sourceProvider.id,
        sourceProviderName: sourceProvider.name,
    };
}

function buildCatalogProvidersForPurpose(catalogProviders: CatalogProvider[], purpose: CatalogPurpose): CatalogProvider[] {
    if (purpose === "chat") {
        return catalogProviders.filter((item) => providerMatchesPurpose(item, purpose));
    }
    const expected = getCatalogPurposeConfig(purpose).modality || purpose;
    const projected = new Map<string, CatalogProvider>();
    const internalCapabilityIds = new Set<string>();
    for (const rootProvider of catalogProviders) {
        const capabilityEntries = (rootProvider.capabilityEntries || []).filter(
            (entry) => String(entry.mediaModality || entry.type || "").toLowerCase() === expected,
        );
        if (!capabilityEntries.length) continue;
        const models = capabilityEntries.flatMap((entry) => {
            if (entry.sourceProviderId) internalCapabilityIds.add(entry.sourceProviderId);
            return (entry.models || []).map((model) => endpointMediaCatalogModel(model, entry, rootProvider));
        });
        projected.set(rootProvider.id, {
            ...rootProvider,
            mediaModality: expected,
            models,
        });
    }
    const direct = catalogProviders.filter((item) => providerMatchesPurpose(item, purpose));
    for (const provider of direct) {
        if (provider.catalogVisibility === "internal_capability" && internalCapabilityIds.has(provider.id)) continue;
        if (!projected.has(provider.id)) projected.set(provider.id, provider);
    }
    const customProviders = catalogProviders.filter((item) => item.isCustom && providerMatchesPurpose(item, purpose));
    for (const provider of customProviders) {
        projected.set(provider.id, provider);
    }
    return Array.from(projected.values());
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
    if (authType === "oauth_file") return purpose === "chat";
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
                {logo ? <Image src={logo} alt="" width={16} height={16} className="h-4 w-4 object-contain" unoptimized /> : initial}
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
    const [platformLoginPreset, setPlatformLoginPreset] = useState<PlatformLoginPreset>("codex");
    const [localBackendPreset, setLocalBackendPreset] = useState<LocalBackendPreset>("ollama");
    const [modelType, setModelType] = useState("TEXT");
    const [modelProviderId, setModelProviderId] = useState("");
    const [rerankApiFlavor, setRerankApiFlavor] = useState("generic");
    const [connectionStatusMap, setConnectionStatusMap] = useState<Record<string, ModelConnectionStatus>>({});
    const [reasoningRepairStatusMap, setReasoningRepairStatusMap] = useState<Record<string, ModelReasoningRepairStatus>>({});
    const [defaultModelRef, setDefaultModelRef] = useState<string | null>(null);
    const [catalogProviders, setCatalogProviders] = useState<CatalogProvider[]>([]);
    const [catalogPurpose, setCatalogPurpose] = useState<CatalogPurpose>("chat");
    const [catalogRuntimeProtocol, setCatalogRuntimeProtocol] = useState<CatalogRuntimeProtocol>("default");
    const [selectedCatalogProviderId, setSelectedCatalogProviderId] = useState("openai");
    const [catalogApiKey, setCatalogApiKey] = useState("");
    const [catalogVoiceAppId, setCatalogVoiceAppId] = useState("");
    const [catalogVoiceResourceId, setCatalogVoiceResourceId] = useState("");
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
    const [audioConfig, setAudioConfig] = useState<AudioRuntimeConfig>(DEFAULT_AUDIO_CONFIG);
    const [isAudioSaving, setIsAudioSaving] = useState(false);
    const [customTtsRemoteVoices, setCustomTtsRemoteVoices] = useState<AudioVoiceOption[]>([]);
    const [isCustomTtsVoiceLoading, setIsCustomTtsVoiceLoading] = useState(false);
    const [modelRefTtsVoices, setModelRefTtsVoices] = useState<AudioVoiceOption[]>([]);
    const [modelRefTtsVoiceInfo, setModelRefTtsVoiceInfo] = useState<TtsVoiceProviderInfo | null>(null);
    const [isModelRefTtsVoiceLoading, setIsModelRefTtsVoiceLoading] = useState(false);
    const [deletingModelRefTtsVoiceId, setDeletingModelRefTtsVoiceId] = useState<string | null>(null);
    const [ttsCloneFile, setTtsCloneFile] = useState<File | null>(null);
    const [ttsCloneVoiceId, setTtsCloneVoiceId] = useState("");
    const [ttsClonePreviewText, setTtsClonePreviewText] = useState("");
    const [isTtsCloning, setIsTtsCloning] = useState(false);
    const fetchData = async () => {
        setIsLoading(true);
        try {
            const response = await fetch("/api/model-hub/bootstrap", { cache: "no-store" });
            const payload = response.ok ? await response.json().catch(() => ({})) : {};
            setProviders(Array.isArray(payload.providers) ? payload.providers : []);
            setModels(Array.isArray(payload.models) ? payload.models : []);
            setHubEnvelope(payload.hubEnvelope || null);
            setAudioConfig(mergeAudioConfig(payload.audioConfig || null));
            const defaultData = payload.defaultModel || {};
            setDefaultModelRef(defaultData.modelRef || defaultData.modelId || defaultData.value || null);
            const catalogData = payload.catalog || {};
            setCatalogProviders(Array.isArray(catalogData.providers) ? catalogData.providers : []);
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
    const apiCatalogProviders = useMemo(() => buildCatalogProvidersForPurpose(catalogProviders, catalogPurpose), [catalogProviders, catalogPurpose]);
    const selectedCatalogProvider = useMemo(
        () => apiCatalogProviders.find((item) => item.id === selectedCatalogProviderId) || catalogProviders.find((item) => item.id === selectedCatalogProviderId) || null,
        [apiCatalogProviders, catalogProviders, selectedCatalogProviderId],
    );
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
    const requiresVolcengineVoiceConfig = useMemo(() => {
        if (catalogPurpose !== "voice") return false;
        const probe = `${selectedCatalogProviderId} ${selectedCatalogProvider?.id || ""} ${selectedCatalogProvider?.name || ""} ${selectedCatalogModelId}`.toLowerCase();
        return probe.includes("volcengine") || probe.includes("doubao-voice");
    }, [catalogPurpose, selectedCatalogModelId, selectedCatalogProvider, selectedCatalogProviderId]);
    const visibleProviders = useMemo(() => {
        const configuredProviderIds = new Set(providers.map((provider) => provider.code || provider.id));
        const capabilityRootsBySource = new Map<string, Set<string>>();
        for (const rootProvider of catalogProviders) {
            for (const capability of rootProvider.capabilityEntries || []) {
                const sourceProviderId = String(capability.sourceProviderId || "").trim();
                if (!sourceProviderId) continue;
                const roots = capabilityRootsBySource.get(sourceProviderId) || new Set<string>();
                roots.add(rootProvider.id);
                capabilityRootsBySource.set(sourceProviderId, roots);
            }
        }
        return providers.filter((provider) => {
            const providerId = provider.code || provider.id;
            const rootIds = capabilityRootsBySource.get(providerId);
            return !rootIds || !Array.from(rootIds).some((rootId) => configuredProviderIds.has(rootId));
        });
    }, [catalogProviders, providers]);
    const visibleCatalogModels = useMemo(() => {
        const query = catalogModelFilter.trim().toLowerCase();
        if (!query) return catalogProbeModels.slice(0, 80);
        return catalogProbeModels
            .filter((model) => `${model.modelId || ""} ${model.id || ""}`.toLowerCase().includes(query))
            .slice(0, 80);
    }, [catalogModelFilter, catalogProbeModels]);
    const sttModelCandidates = useMemo(
        () => models.filter((model) => hasAudioInputCapability(model)),
        [models],
    );
    const ttsModelCandidates = useMemo(
        () => models.filter((model) => hasAudioOutputCapability(model, controlModelsById.get(modelRefFor(model)))),
        [controlModelsById, models],
    );
    const selectedTtsModelRef = audioConfig.tts.model_ref?.modelRef || "";
    const selectedTtsModel = useMemo(
        () => models.find((model) => modelRefFor(model) === selectedTtsModelRef) || null,
        [models, selectedTtsModelRef],
    );
    const isManagedModelRefTtsVoice = audioConfig.tts.active_provider === "model_ref" && isManagedModelRefTtsVoiceModel(selectedTtsModel, selectedTtsModelRef);
    const modelRefTtsVoiceCapabilities = modelRefTtsVoiceInfo?.capabilities || {};
    const customSttProtocol = audioConfig.stt.providers.custom.protocol || "multipart";
    const customTtsProtocol = audioConfig.tts.custom.protocol || "json_audio_stream";
    const ttsVoicePresets = useMemo(() => {
        if (audioConfig.tts.active_provider === "edge-tts") return localizeAudioVoicePresets("edge-tts", t);
        const presetKey = resolveAudioVoicePresetKey(selectedTtsModelRef);
        return presetKey ? localizeAudioVoicePresets(presetKey, t) : [];
    }, [audioConfig.tts.active_provider, selectedTtsModelRef, t]);
    const customTtsVoicePresets = useMemo(() => voicePresetsForCustomTtsProtocol(customTtsProtocol, t), [customTtsProtocol, t]);
    const customTtsVoiceOptions = customTtsRemoteVoices.length > 0 ? customTtsRemoteVoices : customTtsVoicePresets;
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
    useEffect(() => {
        setModelRefTtsVoices([]);
        setModelRefTtsVoiceInfo(null);
        setTtsCloneFile(null);
        setTtsCloneVoiceId("");
        setTtsClonePreviewText("");
    }, [selectedTtsModelRef]);
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
    const handleToggleNoThink = async (model: AIModel, controlMeta: ControlPlaneModel | null, disabled: boolean) => {
        const thinkingControl = {
            ...(model.thinkingControl || {}),
            ...(controlMeta?.thinkingControl || {}),
            supportsNoThink: true,
            disabled,
        };
        const response = await fetch("/api/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                providerId: model.providerId,
                modelId: model.modelId,
                type: model.type || controlMeta?.type || "TEXT",
                contextWindow: model.contextWindow ?? controlMeta?.contextWindow ?? "",
                maxTokens: model.maxTokens ?? controlMeta?.maxTokens ?? "",
                rerankApiFlavor: model.rerankApiFlavor || "",
                thinkingControl,
            }),
        });
        if (!response.ok) {
            const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.thinkingSaveFailed"));
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.page.thinkingSaveFailed"), description: errorMessage });
            return;
        }
        toast({
            title: disabled ? t("app.admin.dashboard.model.hub.page.thinkingDisabled") : t("app.admin.dashboard.model.hub.page.thinkingDefaultRestored"),
            description: model.modelId,
        });
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
    const handleSetDefaultModel = async (modelRef: string, categoryKey?: string) => {
        const response = await fetch("/api/models/defaults", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ modelRef, category: categoryKey }),
        });
        if (!response.ok) {
            const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.kd2b2caac"));
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.page.kd2b2caac"), description: errorMessage });
            return;
        }
        if (!categoryKey || categoryKey === "text_generation") {
            setDefaultModelRef(modelRef);
        }
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
        if (!isCustomProvider && isMediaPurpose && selectedCatalogProvider?.models?.length) {
            const catalogModels = selectedCatalogProvider.models;
            setProbedCatalogProviderId(selectedCatalogProvider.id);
            setCatalogProbeModels(catalogModels);
            setCatalogModelFilter("");
            setSelectedCatalogModelId(catalogModels[0]?.modelId || catalogModels[0]?.id || "");
            setManualModelEntryEnabled(false);
            setCatalogProbeStatus({
                ok: true,
                message: t("app.admin.dashboard.model.hub.catalog.catalogLoaded", { count: catalogModels.length }),
                source: "catalog",
            });
            return;
        }
        if (!isCustomProvider && selectedCatalogProvider && (selectedCatalogProvider.auth?.type === "oauth_file" || selectedCatalogProvider.probeStrategy === "catalog_only")) {
            const catalogModels = Array.isArray(selectedCatalogProvider.models) ? selectedCatalogProvider.models : [];
            setProbedCatalogProviderId(selectedCatalogProvider.id);
            setCatalogProbeModels(catalogModels);
            setCatalogModelFilter("");
            setSelectedCatalogModelId(catalogModels[0]?.modelId || catalogModels[0]?.id || "");
            setManualModelEntryEnabled(catalogModels.length === 0);
            setCatalogProbeStatus({
                ok: true,
                message: catalogModels.length
                    ? t("app.admin.dashboard.model.hub.catalog.catalogLoaded", { count: catalogModels.length })
                    : t("app.admin.dashboard.model.hub.catalog.catalogOnlyManual"),
                source: "catalog",
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
                const reason = extractErrorText(data.error || data.detail || data.reason, t("app.admin.dashboard.model.hub.catalog.fallback"));
                const requiresCredential = data.reason === "credential_required" || String(reason).toLowerCase().includes("api key");
                const catalogOnly = data.reason === "catalog_only_provider";
                const fallbackCatalogModels = catalogOnly && selectedCatalogProvider?.models ? selectedCatalogProvider.models : [];
                setCatalogProbeModels(fallbackCatalogModels);
                setSelectedCatalogModelId(fallbackCatalogModels[0]?.modelId || fallbackCatalogModels[0]?.id || "");
                setProbedCatalogProviderId(fallbackCatalogModels.length ? selectedCatalogProviderId : "");
                setManualModelEntryEnabled(fallbackCatalogModels.length === 0 && (!requiresCredential || catalogOnly));
                setCatalogProbeStatus({
                    ok: catalogOnly && fallbackCatalogModels.length > 0,
                    message: catalogOnly && fallbackCatalogModels.length > 0
                        ? t("app.admin.dashboard.model.hub.catalog.catalogLoaded", { count: fallbackCatalogModels.length })
                        : catalogOnly ? t("app.admin.dashboard.model.hub.catalog.catalogOnlyManual") : requiresCredential ? t("app.admin.dashboard.model.hub.catalog.credentialRequired") : reason,
                    resolvedModelsUrl: data.resolvedModelsUrl,
                    source: catalogOnly && fallbackCatalogModels.length > 0 ? "catalog" : data.source,
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
            const provider = apiCatalogProviders.find((item) => item.id === providerId) || catalogProviders.find((item) => item.id === providerId);
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
                    voiceAppId: requiresVolcengineVoiceConfig ? catalogVoiceAppId.trim() : "",
                    voiceResourceId: requiresVolcengineVoiceConfig ? catalogVoiceResourceId.trim() : "",
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
    const handleSaveAudioConfig = async () => {
        setIsAudioSaving(true);
        try {
            const response = await fetch("/api/audio/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(audioConfig),
            });
            if (!response.ok) {
                const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.audio.saveFailed"));
                toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.saveFailed"), description: errorMessage });
                return;
            }
            setAudioConfig(mergeAudioConfig(await response.json().catch(() => audioConfig)));
            toast({ title: t("app.admin.dashboard.model.hub.audio.saved"), description: t("app.admin.dashboard.model.hub.audio.savedDescription") });
        } finally {
            setIsAudioSaving(false);
        }
    };
    const handleFetchCustomTtsVoices = async () => {
        setIsCustomTtsVoiceLoading(true);
        try {
            const response = await fetch("/api/audio/voices", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    protocol: customTtsProtocol,
                    endpoint: audioConfig.tts.custom.endpoint || "",
                    apiKey: audioConfig.tts.custom.api_key || "",
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceFetchFailed"),
                    description: extractErrorText(payload.error, t("app.admin.dashboard.model.hub.audio.voiceFetchFailed")),
                });
                return;
            }
            const voices = Array.isArray(payload.voices) ? payload.voices : [];
            setCustomTtsRemoteVoices(voices);
            toast({
                title: voices.length > 0 ? t("app.admin.dashboard.model.hub.audio.voiceFetchSuccess") : t("app.admin.dashboard.model.hub.audio.voiceFetchEmpty"),
            });
        } finally {
            setIsCustomTtsVoiceLoading(false);
        }
    };
    const handleFetchModelRefTtsVoices = async () => {
        if (!selectedTtsModelRef) return;
        setIsModelRefTtsVoiceLoading(true);
        try {
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "list", modelRef: selectedTtsModelRef }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceFetchFailed"),
                    description: extractErrorText(payload.error, t("app.admin.dashboard.model.hub.audio.voiceFetchFailed")),
                });
                return;
            }
            const voices = Array.isArray(payload.voices) ? payload.voices : [];
            setModelRefTtsVoices(voices);
            setModelRefTtsVoiceInfo({
                provider: typeof payload.provider === "string" ? payload.provider : undefined,
                capabilities: payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : undefined,
            });
            toast({
                title: voices.length > 0 ? t("app.admin.dashboard.model.hub.audio.voiceFetchSuccess") : t("app.admin.dashboard.model.hub.audio.voiceFetchEmpty"),
            });
        } finally {
            setIsModelRefTtsVoiceLoading(false);
        }
    };
    const handleDeleteModelRefTtsVoice = async (voice: AudioVoiceOption) => {
        if (!selectedTtsModelRef || !voice.value) return;
        setDeletingModelRefTtsVoiceId(voice.value);
        try {
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: "delete",
                    modelRef: selectedTtsModelRef,
                    voiceId: voice.value,
                    voiceType: voice.group === "generated" ? "voice_generation" : "voice_cloning",
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceDeleteFailed"),
                    description: extractErrorText(payload.error, t("app.admin.dashboard.model.hub.audio.voiceDeleteFailed")),
                });
                return;
            }
            setModelRefTtsVoices((current) => current.filter((item) => item.value !== voice.value));
            if (audioConfig.tts.model_ref?.voice === voice.value) setTtsModelRefValue("voice", "");
            toast({ title: t("app.admin.dashboard.model.hub.audio.voiceDeleted") });
        } finally {
            setDeletingModelRefTtsVoiceId(null);
        }
    };
    const handleCloneModelRefTtsVoice = async () => {
        if (!selectedTtsModelRef || !ttsCloneFile || !ttsCloneVoiceId.trim()) {
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.audio.voiceCloneMissing"),
            });
            return;
        }
        setIsTtsCloning(true);
        try {
            const formData = new FormData();
            formData.append("action", "clone_from_upload");
            formData.append("modelRef", selectedTtsModelRef);
            formData.append("voiceId", ttsCloneVoiceId.trim());
            if (ttsClonePreviewText.trim()) formData.append("previewText", ttsClonePreviewText.trim());
            formData.append("file", ttsCloneFile);
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                body: formData,
            });
            const payload = await response.json().catch(() => ({}));
            setModelRefTtsVoiceInfo({
                provider: typeof payload.provider === "string" ? payload.provider : modelRefTtsVoiceInfo?.provider,
                capabilities: payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : modelRefTtsVoiceInfo?.capabilities,
            });
            if (!response.ok || payload.ok === false) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceCloneFailed"),
                    description: extractErrorText(payload.error, t("app.admin.dashboard.model.hub.audio.voiceCloneFailed")),
                });
                return;
            }
            const clonedVoiceId = String(payload.voiceId || ttsCloneVoiceId.trim());
            setTtsModelRefValue("voice", clonedVoiceId);
            setTtsCloneFile(null);
            setTtsCloneVoiceId("");
            setTtsClonePreviewText("");
            toast({ title: t("app.admin.dashboard.model.hub.audio.voiceCloneSuccess"), description: clonedVoiceId });
            await handleFetchModelRefTtsVoices();
        } finally {
            setIsTtsCloning(false);
        }
    };
    const setSttProviderValue = (provider: "custom" | "baidu", key: string, value: string) => {
        setAudioConfig((current) => ({
            ...current,
            stt: {
                ...current.stt,
                providers: {
                    ...current.stt.providers,
                    [provider]: {
                        ...current.stt.providers[provider],
                        [key]: value,
                    },
                },
            },
        }));
    };
    const setSttModelRefValue = (key: string, value: string) => {
        setAudioConfig((current) => ({
            ...current,
            stt: {
                ...current.stt,
                model_ref: {
                    ...current.stt.model_ref,
                    [key]: value,
                },
            },
        }));
    };
    const setTtsValue = (provider: "edge_tts" | "custom", key: string, value: string) => {
        setAudioConfig((current) => ({
            ...current,
            tts: {
                ...current.tts,
                [provider]: {
                    ...current.tts[provider],
                    [key]: value,
                },
            },
        }));
    };
    const setTtsModelRefValue = (key: string, value: string) => {
        setAudioConfig((current) => ({
            ...current,
            tts: {
                ...current.tts,
                model_ref: {
                    ...current.tts.model_ref,
                    [key]: value,
                },
            },
        }));
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
            const protocolWarning = typeof data?.protocolWarningMessage === "string" ? data.protocolWarningMessage : "";
            const recommendedRoute = [
                data?.recommendedApiStandard ? `protocol: ${data.recommendedApiStandard}` : "",
                data?.recommendedBaseUrl ? `baseURL: ${data.recommendedBaseUrl}` : "",
            ].filter(Boolean).join(" / ");
            const capabilityChecks = data?.capabilityChecks && typeof data.capabilityChecks === "object" ? Object.values(data.capabilityChecks) : [];
            const skippedCapabilities = capabilityChecks.filter((item) => (item as { status?: string })?.status === "skipped").length;
            const successMessage = [
                protocolWarning,
                `${data.providerName || "Provider"}${providerPreset ? `/${providerPreset}` : ""} · ${Math.round(Number(data.latencyMs || 0))}ms`,
                resolvedEndpoint,
                recommendedRoute,
                skippedCapabilities ? t("app.admin.dashboard.model.hub.catalog.messages.skippedCapabilities") : "",
                data.message || "",
            ].filter(Boolean).join(" · ");
            setConnectionStatusMap((current) => ({
                ...current,
                [modelRef]: { status: protocolWarning ? "warning" : "success", message: successMessage },
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
    const handleRepairReasoning = async (modelRef: string) => {
        setReasoningRepairStatusMap((current) => ({
            ...current,
            [modelRef]: { status: "repairing", message: t("app.admin.dashboard.model.hub.reasoningRepair.running") },
        }));
        try {
            const target = models.find((item) => (item.modelRef || item.id) === modelRef);
            const response = await fetch("/api/models/repair-reasoning", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ modelRef, modelId: target?.modelId, providerId: target?.providerId }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.ok === false) {
                const errorMessage = extractErrorText(data?.detail, "")
                    || extractErrorText(data?.error, "")
                    || t("app.admin.dashboard.model.hub.reasoningRepair.failed");
                setReasoningRepairStatusMap((current) => ({
                    ...current,
                    [modelRef]: { status: "error", message: errorMessage },
                }));
                toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.reasoningRepair.failed"), description: errorMessage });
                return;
            }
            const status: ModelReasoningRepairStatus["status"] = data.saveStatus === "saved" ? "success" : data.status === "no_visible_reasoning_field" || data.status === "no_reasoning_signal" ? "warning" : "success";
            const message = [
                data.matchedField ? `${t("app.admin.dashboard.model.hub.reasoningRepair.field")}: ${data.matchedField}` : "",
                data.saveStatus ? `${t("app.admin.dashboard.model.hub.reasoningRepair.saveStatus")}: ${data.saveStatus}` : "",
                data.status || "",
            ].filter(Boolean).join(" · ") || t("app.admin.dashboard.model.hub.reasoningRepair.done");
            setReasoningRepairStatusMap((current) => ({
                ...current,
                [modelRef]: { status, message },
            }));
            toast({
                title: status === "warning" ? t("app.admin.dashboard.model.hub.reasoningRepair.noField") : t("app.admin.dashboard.model.hub.reasoningRepair.success"),
                description: message,
            });
            if (data.saveStatus === "saved") {
                await fetchData();
            }
        }
        catch (error) {
            const errorMessage = error instanceof Error ? error.message : t("app.admin.dashboard.model.hub.reasoningRepair.failed");
            setReasoningRepairStatusMap((current) => ({
                ...current,
                [modelRef]: { status: "error", message: errorMessage },
            }));
        }
    };
    const systemAudioConfigCard = (
        <ConfigCard title={t("app.admin.dashboard.model.hub.audio.systemTitle")} description={t("app.admin.dashboard.model.hub.audio.systemDescription")} variant="list" allowOverflow>
            <div className="grid gap-4 xl:grid-cols-2">
                <div className="rounded-2xl border bg-card p-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                <Mic className="h-4 w-4 text-slate-500" />
                                {t("app.admin.dashboard.model.hub.audio.sttTitle")}
                            </div>
                        </div>
                        <Badge variant="secondary">{audioConfig.stt.active_provider}</Badge>
                    </div>
                    <div className="mt-4 space-y-3">
                        <div className="space-y-1.5">
                            <Label>{t("app.admin.dashboard.model.hub.audio.providerMode")}</Label>
                            <Select value={audioConfig.stt.active_provider} onValueChange={(value) => setAudioConfig((current) => ({ ...current, stt: { ...current.stt, active_provider: value } }))}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="baidu">{t("app.admin.dashboard.model.hub.audio.baiduStt")}</SelectItem>
                                    <SelectItem value="custom">{t("app.admin.dashboard.model.hub.audio.customStt")}</SelectItem>
                                    <SelectItem value="model_ref">{t("app.admin.dashboard.model.hub.audio.modelRefProvider")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {audioConfig.stt.active_provider === "baidu" ? (
                            <div className="grid gap-3 rounded-xl border border-dashed p-3">
                                <Input value={audioConfig.stt.providers.baidu.app_id || ""} onChange={(event) => setSttProviderValue("baidu", "app_id", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.appIdOptional")} />
                                <Input value={audioConfig.stt.providers.baidu.api_key || ""} onChange={(event) => setSttProviderValue("baidu", "api_key", event.target.value)} placeholder="API Key" />
                                <Input type="password" value={audioConfig.stt.providers.baidu.secret_key || ""} onChange={(event) => setSttProviderValue("baidu", "secret_key", event.target.value)} placeholder="Secret Key" />
                            </div>
                        ) : null}
                        {audioConfig.stt.active_provider === "custom" ? (
                            <div className="grid gap-3 rounded-xl border border-dashed p-3">
                                <Select value={customSttProtocol} onValueChange={(value) => setSttProviderValue("custom", "protocol", value)}>
                                    <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.protocol")} /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="multipart">{t("app.admin.dashboard.model.hub.audio.protocol.multipart")}</SelectItem>
                                        <SelectItem value="openai_transcription">{t("app.admin.dashboard.model.hub.audio.protocol.openaiTranscription")}</SelectItem>
                                        <SelectItem value="json_base64">{t("app.admin.dashboard.model.hub.audio.protocol.jsonBase64")}</SelectItem>
                                    </SelectContent>
                                </Select>
                                <Input value={audioConfig.stt.providers.custom.endpoint || ""} onChange={(event) => setSttProviderValue("custom", "endpoint", event.target.value)} placeholder={sttEndpointPlaceholder(customSttProtocol)} />
                                <Input type="password" value={audioConfig.stt.providers.custom.api_key || ""} onChange={(event) => setSttProviderValue("custom", "api_key", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.apiKeyOptional")} />
                                <div className="grid gap-3 md:grid-cols-2">
                                    <Input value={audioConfig.stt.providers.custom.model || ""} onChange={(event) => setSttProviderValue("custom", "model", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.modelPlaceholder")} />
                                    <Input value={audioConfig.stt.providers.custom.language || ""} onChange={(event) => setSttProviderValue("custom", "language", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.languagePlaceholder")} />
                                </div>
                                <div className="grid gap-3 md:grid-cols-2">
                                    <Input value={audioConfig.stt.providers.custom.fileField || ""} onChange={(event) => setSttProviderValue("custom", "fileField", event.target.value)} placeholder={customSttProtocol === "json_base64" ? t("app.admin.dashboard.model.hub.audio.audioFieldPlaceholder") : t("app.admin.dashboard.model.hub.audio.fileFieldPlaceholder")} />
                                    <Input value={audioConfig.stt.providers.custom.responseTextPath || ""} onChange={(event) => setSttProviderValue("custom", "responseTextPath", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.responseTextPathPlaceholder")} />
                                </div>
                                <Input value={headerValueForInput(audioConfig.stt.providers.custom.headers)} onChange={(event) => setSttProviderValue("custom", "headers", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.headersPlaceholder")} />
                            </div>
                        ) : null}
                        {audioConfig.stt.active_provider === "model_ref" ? (
                            <div className="grid gap-3 rounded-xl border border-dashed p-3">
                                <Select value={audioConfig.stt.model_ref?.modelRef || ""} onValueChange={(value) => setSttModelRefValue("modelRef", value)}>
                                    <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.selectSttModel")} /></SelectTrigger>
                                    <SelectContent>
                                        {sttModelCandidates.map((model) => {
                                            const ref = modelRefFor(model);
                                            return <SelectItem key={ref} value={ref}>{modelLabel(model)}</SelectItem>;
                                        })}
                                    </SelectContent>
                                </Select>
                                <div className="grid gap-3 md:grid-cols-2">
                                    <Input value={audioConfig.stt.model_ref?.language || ""} onChange={(event) => setSttModelRefValue("language", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.languagePlaceholder")} />
                                    <Input value={audioConfig.stt.model_ref?.mode || ""} onChange={(event) => setSttModelRefValue("mode", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.modePlaceholder")} />
                                </div>
                                <Input value={audioConfig.stt.model_ref?.prompt || ""} onChange={(event) => setSttModelRefValue("prompt", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.promptPlaceholder")} />
                            </div>
                        ) : null}
                    </div>
                </div>
                <div className="rounded-2xl border bg-card p-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                <Volume2 className="h-4 w-4 text-slate-500" />
                                {t("app.admin.dashboard.model.hub.audio.ttsTitle")}
                            </div>
                        </div>
                        <Badge variant="secondary">{audioConfig.tts.active_provider}</Badge>
                    </div>
                    <div className="mt-4 space-y-3">
                        <div className="space-y-1.5">
                            <Label>{t("app.admin.dashboard.model.hub.audio.providerMode")}</Label>
                            <Select value={audioConfig.tts.active_provider} onValueChange={(value) => setAudioConfig((current) => ({ ...current, tts: { ...current.tts, active_provider: value } }))}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="edge-tts">Edge TTS</SelectItem>
                                    <SelectItem value="custom">{t("app.admin.dashboard.model.hub.audio.customTts")}</SelectItem>
                                    <SelectItem value="model_ref">{t("app.admin.dashboard.model.hub.audio.modelRefProvider")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {audioConfig.tts.active_provider === "edge-tts" ? (
                            <div className="grid gap-3 rounded-xl border border-dashed p-3">
                                <Select value={audioConfig.tts.edge_tts.voice || "zh-CN-XiaoxiaoNeural"} onValueChange={(value) => setTtsValue("edge_tts", "voice", value)}>
                                    <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.selectVoice")} /></SelectTrigger>
                                    <SelectContent>
                                        {ttsVoicePresets.map((voice) => <SelectItem key={voice.value} value={voice.value}>{voice.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                                <div className="grid gap-3 md:grid-cols-2">
                                    <Input value={audioConfig.tts.edge_tts.rate || ""} onChange={(event) => setTtsValue("edge_tts", "rate", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.ratePlaceholder")} />
                                    <Input value={audioConfig.tts.edge_tts.volume || ""} onChange={(event) => setTtsValue("edge_tts", "volume", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.volumePlaceholder")} />
                                </div>
                            </div>
                        ) : null}
                        {audioConfig.tts.active_provider === "custom" ? (
                            <div className="grid gap-3 rounded-xl border border-dashed p-3">
                                <Select value={customTtsProtocol} onValueChange={(value) => { setCustomTtsRemoteVoices([]); setTtsValue("custom", "protocol", value); }}>
                                    <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.protocol")} /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="json_audio_stream">{t("app.admin.dashboard.model.hub.audio.protocol.jsonAudioStream")}</SelectItem>
                                        <SelectItem value="openai_speech">{t("app.admin.dashboard.model.hub.audio.protocol.openaiSpeech")}</SelectItem>
                                        <SelectItem value="minimax_t2a_v2">{t("app.admin.dashboard.model.hub.audio.protocol.minimaxT2a")}</SelectItem>
                                    </SelectContent>
                                </Select>
                                <Input value={audioConfig.tts.custom.endpoint || ""} onChange={(event) => setTtsValue("custom", "endpoint", event.target.value)} placeholder={ttsEndpointPlaceholder(customTtsProtocol)} />
                                <Input type="password" value={audioConfig.tts.custom.api_key || ""} onChange={(event) => setTtsValue("custom", "api_key", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.apiKeyOptional")} />
                                {customTtsProtocol === "minimax_t2a_v2" ? (
                                    <Button type="button" variant="outline" size="sm" onClick={() => void handleFetchCustomTtsVoices()} disabled={isCustomTtsVoiceLoading || !audioConfig.tts.custom.api_key}>
                                        <RefreshCw className={`mr-2 h-4 w-4 ${isCustomTtsVoiceLoading ? "animate-spin" : ""}`} />
                                        {t("app.admin.dashboard.model.hub.audio.fetchVoices")}
                                    </Button>
                                ) : null}
                                {customTtsVoiceOptions.length > 0 ? (
                                    <Select value={customTtsVoiceOptions.some((item) => item.value === audioConfig.tts.custom.voice) ? audioConfig.tts.custom.voice : undefined} onValueChange={(value) => setTtsValue("custom", "voice", value)}>
                                        <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.selectVoice")} /></SelectTrigger>
                                        <SelectContent>
                                            {customTtsVoiceOptions.map((voice) => <SelectItem key={voice.value} value={voice.value}>{voice.label}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                ) : null}
                                <Input value={audioConfig.tts.custom.voice || ""} onChange={(event) => setTtsValue("custom", "voice", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.voicePlaceholder")} />
                                <div className="grid gap-3 md:grid-cols-3">
                                    <Input value={audioConfig.tts.custom.model || ""} onChange={(event) => setTtsValue("custom", "model", event.target.value)} placeholder={customTtsProtocol === "minimax_t2a_v2" ? "speech-2.8-turbo" : t("app.admin.dashboard.model.hub.audio.modelPlaceholder")} />
                                    <Input value={audioConfig.tts.custom.format || ""} onChange={(event) => setTtsValue("custom", "format", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.formatPlaceholder")} />
                                    <Input value={audioConfig.tts.custom.speed || ""} onChange={(event) => setTtsValue("custom", "speed", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.speedPlaceholder")} />
                                </div>
                                <Input value={audioConfig.tts.custom.responseAudioPath || ""} onChange={(event) => setTtsValue("custom", "responseAudioPath", event.target.value)} placeholder={customTtsProtocol === "minimax_t2a_v2" ? "data.audio" : t("app.admin.dashboard.model.hub.audio.responseAudioPathPlaceholder")} />
                                <Input value={headerValueForInput(audioConfig.tts.custom.headers)} onChange={(event) => setTtsValue("custom", "headers", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.headersPlaceholder")} />
                            </div>
                        ) : null}
                        {audioConfig.tts.active_provider === "model_ref" ? (
                            <div className="grid gap-3 rounded-xl border border-dashed p-3">
                                <Select value={audioConfig.tts.model_ref?.modelRef || ""} onValueChange={(value) => setTtsModelRefValue("modelRef", value)}>
                                    <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.selectTtsModel")} /></SelectTrigger>
                                    <SelectContent>
                                        {ttsModelCandidates.map((model) => {
                                            const ref = modelRefFor(model);
                                            return <SelectItem key={ref} value={ref}>{modelLabel(model)}</SelectItem>;
                                        })}
                                    </SelectContent>
                                </Select>
                                {isManagedModelRefTtsVoice ? (
                                    <div className="grid gap-3 rounded-xl border bg-muted/20 p-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <Label>{t("app.admin.dashboard.model.hub.audio.modelRefVoiceManager")}</Label>
                                            <Button type="button" variant="outline" size="sm" onClick={() => void handleFetchModelRefTtsVoices()} disabled={isModelRefTtsVoiceLoading || !selectedTtsModelRef}>
                                                <RefreshCw className={`mr-2 h-4 w-4 ${isModelRefTtsVoiceLoading ? "animate-spin" : ""}`} />
                                                {t("app.admin.dashboard.model.hub.audio.fetchVoices")}
                                            </Button>
                                        </div>
                                        {modelRefTtsVoices.length > 0 ? (
                                            <div className="grid max-h-48 gap-2 overflow-y-auto rounded-lg border bg-background p-2">
                                                {modelRefTtsVoices.map((voice) => (
                                                    <div key={voice.value} className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 hover:bg-muted">
                                                        <button
                                                            type="button"
                                                            className="min-w-0 flex-1 truncate text-left text-sm"
                                                            onClick={() => setTtsModelRefValue("voice", voice.value)}
                                                            title={voice.label}
                                                        >
                                                            {voice.label}
                                                        </button>
                                                        {voice.deletable && modelRefTtsVoiceCapabilities.supportsDelete !== false ? (
                                                            <Button type="button" variant="ghost" size="icon" className="h-7 w-7" onClick={() => void handleDeleteModelRefTtsVoice(voice)} disabled={deletingModelRefTtsVoiceId === voice.value}>
                                                                <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                                                            </Button>
                                                        ) : null}
                                                    </div>
                                                ))}
                                            </div>
                                        ) : null}
                                        <div className="grid gap-3 md:grid-cols-2">
                                            <Input value={audioConfig.tts.model_ref?.voice || ""} onChange={(event) => setTtsModelRefValue("voice", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.customVoicePlaceholder")} />
                                            <Input value={audioConfig.tts.model_ref?.format || ""} onChange={(event) => setTtsModelRefValue("format", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.formatPlaceholder")} />
                                        </div>
                                        {modelRefTtsVoiceCapabilities.supportsCloneUpload !== false ? (
                                        <div className="grid gap-2 rounded-lg border border-dashed bg-background p-3">
                                            <Label>{t("app.admin.dashboard.model.hub.audio.voiceCloneTitle")}</Label>
                                            <Input value={ttsCloneVoiceId} onChange={(event) => setTtsCloneVoiceId(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.voiceCloneIdPlaceholder")} />
                                            <Input value={ttsClonePreviewText} onChange={(event) => setTtsClonePreviewText(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.voiceClonePreviewPlaceholder")} />
                                            <Input type="file" accept="audio/*" onChange={(event) => setTtsCloneFile(event.target.files?.[0] || null)} />
                                            <Button type="button" variant="outline" size="sm" onClick={() => void handleCloneModelRefTtsVoice()} disabled={isTtsCloning || !ttsCloneFile || !ttsCloneVoiceId.trim()}>
                                                <Upload className="mr-2 h-4 w-4" />
                                                {isTtsCloning ? t("app.admin.dashboard.model.hub.audio.voiceCloning") : t("app.admin.dashboard.model.hub.audio.voiceCloneUpload")}
                                            </Button>
                                        </div>
                                        ) : null}
                                    </div>
                                ) : ttsVoicePresets.length > 0 ? (
                                    <Select value={ttsVoicePresets.some((item) => item.value === audioConfig.tts.model_ref?.voice) ? audioConfig.tts.model_ref?.voice : undefined} onValueChange={(value) => setTtsModelRefValue("voice", value)}>
                                        <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.selectVoice")} /></SelectTrigger>
                                        <SelectContent>
                                            {ttsVoicePresets.map((voice) => <SelectItem key={voice.value} value={voice.value}>{voice.label}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                ) : null}
                                {!isManagedModelRefTtsVoice ? (
                                    <div className="grid gap-3 md:grid-cols-2">
                                        <Input value={audioConfig.tts.model_ref?.voice || ""} onChange={(event) => setTtsModelRefValue("voice", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.customVoicePlaceholder")} />
                                        <Input value={audioConfig.tts.model_ref?.format || ""} onChange={(event) => setTtsModelRefValue("format", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.formatPlaceholder")} />
                                    </div>
                                ) : null}
                                <Input value={audioConfig.tts.model_ref?.speed || ""} onChange={(event) => setTtsModelRefValue("speed", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.speedPlaceholder")} />
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>
            <div className="mt-4 flex justify-end">
                <Button size="sm" onClick={() => void handleSaveAudioConfig()} disabled={isAudioSaving}>
                    <Save className="mr-2 h-4 w-4" />
                    {isAudioSaving ? t("app.admin.dashboard.model.hub.audio.saving") : t("app.admin.dashboard.model.hub.audio.save")}
                </Button>
            </div>
        </ConfigCard>
    );
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
                setPlatformLoginPreset("codex");
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
                            <HydrationSafeClientOnly fallback={<div className="h-10 rounded-md border bg-background px-3 py-2 text-sm text-muted-foreground">{selectedCatalogProvider?.name || t("app.admin.dashboard.model.hub.catalog.selectProvider")}</div>}>
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
                                                <ProviderOptionLabel provider={provider} suffix={provider.auth?.type === "oauth_file" ? "OAuth" : t("app.admin.dashboard.model.hub.catalog.customSuffix")} />
                                            </SelectItem>
                                        ))}
                                        {apiCatalogProviders.filter((item) => !item.isCustom).map((provider) => (
                                            <SelectItem key={provider.id} value={provider.id}>
                                                <ProviderOptionLabel provider={provider} suffix={provider.auth?.type === "oauth_file" ? "OAuth" : undefined} />
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </HydrationSafeClientOnly>
                            {selectedCatalogProvider?.auth?.type === "oauth_file" ? (
                                <div className="flex min-w-0 items-center gap-2 rounded-xl border border-input bg-background px-3 text-sm text-muted-foreground">
                                    <Badge variant="secondary" className="shrink-0">OAuth</Badge>
                                    <span className="truncate">{selectedCatalogProvider.auth.path || selectedCatalogProvider.name}</span>
                                </div>
                            ) : (
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
                            )}
                            <Button disabled={isCatalogBusy} onClick={() => void handleProbeCatalogProvider()}>{t("app.admin.dashboard.model.hub.catalog.probe")}</Button>
                        </div>
                        {requiresVolcengineVoiceConfig ? (
                            <div className="mt-3 grid gap-3 md:grid-cols-2">
                                <Input value={catalogVoiceAppId} onChange={(event) => setCatalogVoiceAppId(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.catalog.voiceAppIdPlaceholder")} />
                                <Input value={catalogVoiceResourceId} onChange={(event) => setCatalogVoiceResourceId(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.catalog.voiceResourceIdPlaceholder")} />
                            </div>
                        ) : null}
                        <div className="mt-2 text-xs text-muted-foreground">
                            {t("app.admin.dashboard.model.hub.catalog.currentPurpose", { purpose: catalogPurposeLabel, hint: catalogPurposeHint })}
                        </div>
                        {catalogPurpose === "chat" && selectedCatalogProvider?.anthropicCompatible?.baseUrl ? (
                            <div className="mt-3 grid gap-2 rounded-xl border border-dashed px-3 py-2">
                                <Label className="text-xs font-semibold">{t("app.admin.dashboard.model.hub.catalog.runtimeProtocol")}</Label>
                                <HydrationSafeClientOnly fallback={<div className="h-9 rounded-md border bg-background px-3 py-2 text-sm text-slate-700">{catalogRuntimeProtocol === "anthropic" ? t("app.admin.dashboard.model.hub.catalog.runtimeProtocolAnthropic") : t("app.admin.dashboard.model.hub.catalog.runtimeProtocolDefault")}</div>}>
                                    <Select value={catalogRuntimeProtocol} onValueChange={(value: CatalogRuntimeProtocol) => setCatalogRuntimeProtocol(value)}>
                                        <SelectTrigger className="h-9">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="default">{t("app.admin.dashboard.model.hub.catalog.runtimeProtocolDefault")}</SelectItem>
                                            <SelectItem value="anthropic">{t("app.admin.dashboard.model.hub.catalog.runtimeProtocolAnthropic")}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </HydrationSafeClientOnly>
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
                                                            {modelIcon ? <Image src={modelIcon} alt="" width={16} height={16} className="h-4 w-4 object-contain" unoptimized /> : null}
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
            </ConfigCard>

            <ConfigCard title={t("app.admin.dashboard.model.hub.page.kd0251a96")} description={t("app.admin.dashboard.model.hub.page.k79d4e8e7")} variant="list" allowOverflow>
                {visibleProviders.length === 0 ? (<EmptyState title={t("app.admin.dashboard.model.hub.page.k8d04b4ed")} description={t("app.admin.dashboard.model.hub.page.k9e469730")}/>) : (<div className="grid gap-3 md:grid-cols-3 2xl:grid-cols-5">
                        {visibleProviders.map((provider) => (<ProviderCard key={provider.id} provider={provider} health={providerOverviewById.get(provider.code) || providerOverviewById.get(provider.id) || null} onEdit={() => {
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
                    <HydrationSafeClientOnly
                        fallback={
                            <div className="grid w-full max-w-5xl grid-cols-4 rounded-2xl bg-slate-100 p-1 text-center text-sm md:grid-cols-6 xl:grid-cols-11">
                                {[t("app.admin.dashboard.model.hub.page.ke8cc995b"), t("app.admin.dashboard.model.hub.page.kc4eaa582"), t("app.admin.dashboard.model.hub.page.k2d2f7b56"), t("app.admin.dashboard.model.hub.catalog.tabImage"), t("app.admin.dashboard.model.hub.catalog.tabVideo"), t("app.admin.dashboard.model.hub.catalog.tabVoice"), t("app.admin.dashboard.model.hub.catalog.tabMusic"), t("app.admin.dashboard.model.hub.catalog.tabWorkflow"), t("app.admin.dashboard.model.hub.catalog.tabModel3d"), t("app.admin.dashboard.model.hub.page.kc1798b61"), t("app.admin.dashboard.model.hub.page.k81ac6b74")].map((label, index) => (
                                    <span key={`${label}-${index}`} className="rounded-md px-3 py-1 text-slate-600">{label}</span>
                                ))}
                            </div>
                        }
                    >
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
                    </HydrationSafeClientOnly>
                </div>

                {filteredModels.length === 0 ? (<EmptyState title={t("app.admin.dashboard.model.hub.page.k14457a61")} description={t("app.admin.dashboard.model.hub.page.k8d6baa0f")}/>) : (<div className="grid gap-3 md:grid-cols-3 2xl:grid-cols-5">
                        {filteredModels.map((model) => {
                            const modelRef = model.modelRef || model.id;
                            const controlMeta = controlModelsById.get(modelRef) || null;
                            return (<ModelCardV2 key={modelRef} model={model} controlMeta={controlMeta} isDefault={modelRef === defaultModelRef} connectionStatus={connectionStatusMap[modelRef] || null} reasoningRepairStatus={reasoningRepairStatusMap[modelRef] || null} onEdit={() => {
                    setEditingModel(model);
                    setModelType(model.type || "TEXT");
                    setModelProviderId(model.providerId);
                    setRerankApiFlavor(model.rerankApiFlavor || "generic");
                    setIsModelDialogOpen(true);
                }} onDelete={handleDeleteModel} onTestConnection={handleTestConnection} onRepairReasoning={handleRepairReasoning} onToggleNoThink={(disabled) => handleToggleNoThink(model, controlMeta, disabled)} onSetDefault={handleSetDefaultModel}/>);
                        })}
                    </div>)}
            </ConfigCard>

            {systemAudioConfigCard}

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
                                    <SelectTrigger id="provider-type"><SelectValue>{resolveAdminLabel(t, "providerType", providerType)}</SelectValue></SelectTrigger>
                                <SelectContent>
                                    {getAdminOptions("providerType").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
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
                                    <Input id="provider-api-standard-readonly" value={resolveAdminLabel(t, "providerApiStandard", providerApiStandard)} readOnly/>
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
                                    <SelectTrigger id="provider-api-standard"><SelectValue>{resolveAdminLabel(t, "providerApiStandard", providerApiStandard)}</SelectValue></SelectTrigger>
                                    <SelectContent>
                                        {getAdminOptions("providerApiStandard").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
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
                                    <SelectTrigger id="provider-credential-mode"><SelectValue>{resolveAdminLabel(t, "providerCredentialMode", providerCredentialMode)}</SelectValue></SelectTrigger>
                                    <SelectContent>
                                        {getAdminOptions("providerCredentialMode").map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
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
                            <Label htmlFor="provider-api-key">{t("admin.enums.providerCredentialMode.apiKey")}</Label>
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
                                <SelectTrigger id="model-type"><SelectValue>{resolveAdminLabel(t, "modelType", modelType)}</SelectValue></SelectTrigger>
                                <SelectContent>
                                    {["TEXT", "MULTIMODAL", "IMAGE", "VIDEO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D", "MEDIA", "EMBEDDING", "RERANK"].map((value) => <SelectItem key={value} value={value}>{resolveAdminLabel(t, "modelType", value)}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        {modelType === "RERANK" ? (<div className="space-y-2">
                                <Label htmlFor="model-rerank-flavor">{t("app.admin.dashboard.model.hub.page.k51b60583")}</Label>
                                <input type="hidden" name="rerankApiFlavor" value={rerankApiFlavor}/>
                                <Select value={rerankApiFlavor} onValueChange={setRerankApiFlavor}>
                                    <SelectTrigger id="model-rerank-flavor"><SelectValue>{resolveAdminLabel(t, "rerankApiFlavor", rerankApiFlavor)}</SelectValue></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="generic">{resolveAdminLabel(t, "rerankApiFlavor", "generic")}</SelectItem>
                                        <SelectItem value="vllm">{resolveAdminLabel(t, "localBackendPreset", "vllm")}</SelectItem>
                                        <SelectItem value="nexa">{resolveAdminLabel(t, "localBackendPreset", "nexa")}</SelectItem>
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
                        ) : RETRIEVAL_MODEL_TYPES.has(modelType) ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="model-context-window">{t("app.admin.dashboard.model.hub.page.retrievalInputWindow")}</Label>
                                    <Input id="model-context-window" name="contextWindow" type="number" defaultValue={editingModel?.contextWindow ?? ""} placeholder={t("app.admin.dashboard.model.hub.page.retrievalInputWindowPlaceholder")}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="model-max-tokens">{t("app.admin.dashboard.model.hub.page.retrievalMaxTokens")}</Label>
                                    <Input id="model-max-tokens" name="maxTokens" type="number" defaultValue={editingModel?.maxTokens ?? ""} placeholder={t("app.admin.dashboard.model.hub.page.retrievalMaxTokensPlaceholder")}/>
                                </div>
                                <p className="md:col-span-2 text-xs text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.page.retrievalInputWindowHelp")}
                                </p>
                            </div>
                        ) : MEDIA_MODEL_TYPES.has(modelType) ? (
                            <div>
                                <AdminHoverInfo
                                    content={t("app.admin.dashboard.model.hub.catalog.mediaModelNotice")}
                                    panelClassName="text-xs leading-5"
                                >
                                    <Badge variant="secondary">
                                        {t("app.admin.dashboard.model.hub.catalog.mediaModelNoticeTitle")}
                                    </Badge>
                                </AdminHoverInfo>
                            </div>
                        ) : null}
                        <Button type="submit" className="w-full">{t("app.admin.dashboard.model.hub.page.kb7dfaded")}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </AdminPageShell>);
}
