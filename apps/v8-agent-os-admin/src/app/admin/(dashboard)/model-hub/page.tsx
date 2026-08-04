"use client";
import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, ExternalLink, LoaderCircle, Mic, Plus, RefreshCw, Save, Trash2, Upload, Volume2, X } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdminSurfaceCard } from "@/components/admin-shell/AdminSurfaceCard";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import type { ControlPlaneModel, ProviderOverview } from "@/components/models/control-plane-types";
import { ProviderCard } from "@/components/models/ProviderCard";
import { ModelCardV2 } from "@/components/models/ModelCardV2";
import { SearchableVoiceSelect } from "@/components/models/SearchableVoiceSelect";
import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { HydrationSafeClientOnly } from "@/components/ui/hydration-safe-client-only";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";
import type { ConfigRegistryEnvelope } from "@/lib/config-registry";
import { getAdminOptions, resolveAdminLabel } from "@/lib/admin-labels";
import audioVoicePresets from "@/lib/models/audio-voice-presets.json";
import { resolveModelIcon, resolveProviderLogo } from "@/lib/models/model-assets";
import { deriveMediaOperationKinds, getMediaCapabilityOptions, resolveMediaCapabilityModes } from "@/lib/models/media-capabilities";
import { getLocalBackendPresetConfig, getPlatformLoginPresetConfig, inferPlatformLoginPreset, inferLocalBackendPreset, LOCAL_BACKEND_PRESETS, PLATFORM_LOGIN_PRESETS, type LocalBackendPreset, type PlatformLoginPreset, type ProviderChannel, } from "@/lib/models/provider-admin";
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
    channels?: ProviderChannel[];
    defaultChannelId?: string;
    channelsSource?: string;
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
    reasoningEffortControl?: Record<string, unknown> | null;
    operationKinds?: string[];
    mediaLimits?: Record<string, unknown> | null;
    endpointBinding?: Record<string, unknown> | null;
    logoAsset?: string | null;
    isEnabled: boolean;
    provider?: {
        id?: string;
        name: string;
        icon?: string | null;
        logoAsset?: string | null;
        baseUrl?: string | null;
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
    channels?: ProviderChannel[];
    defaultChannelId?: string;
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
    declaredCapabilities?: string[];
    models?: CatalogModel[];
};
type CatalogPurpose = "chat" | "image" | "video" | "voice" | "music" | "workflow" | "model3d";
type CustomProviderCapability = "text" | "vision" | "image" | "video" | "voice" | "music" | "model3d";
type CatalogRuntimeProtocol = string;
type ModelWireProtocol = "" | "openai.chat_completions" | "openai.responses" | "anthropic.messages" | "gemini.generate_content";
type ComfyWorkflowDraft = {
    promptJson: string;
    imageNodeId: string;
    imageInputName: string;
    videoNodeId: string;
    videoInputName: string;
    outputNodeId: string;
    outputField: string;
};

const EMPTY_COMFY_WORKFLOW: ComfyWorkflowDraft = {
    promptJson: "",
    imageNodeId: "",
    imageInputName: "",
    videoNodeId: "",
    videoInputName: "",
    outputNodeId: "",
    outputField: "",
};

function comfyWorkflowDraft(mediaLimits: Record<string, unknown> | null | undefined): ComfyWorkflowDraft {
    const workflow = mediaLimits?.comfyuiWorkflow && typeof mediaLimits.comfyuiWorkflow === "object"
        ? mediaLimits.comfyuiWorkflow as Record<string, unknown>
        : {};
    const bindings = workflow.bindings && typeof workflow.bindings === "object"
        ? workflow.bindings as Record<string, Record<string, unknown>>
        : {};
    const output = workflow.output && typeof workflow.output === "object"
        ? workflow.output as Record<string, unknown>
        : {};
    const prompt = workflow.prompt && typeof workflow.prompt === "object" ? workflow.prompt : null;
    return {
        promptJson: prompt ? JSON.stringify(prompt, null, 2) : "",
        imageNodeId: String(bindings.image?.nodeId || ""),
        imageInputName: String(bindings.image?.inputName || ""),
        videoNodeId: String(bindings.video?.nodeId || ""),
        videoInputName: String(bindings.video?.inputName || ""),
        outputNodeId: String(output.nodeId || ""),
        outputField: String(output.field || ""),
    };
}

const MODEL_WIRE_PROTOCOLS: Array<{ id: Exclude<ModelWireProtocol, "">; labelKey: string }> = [
    { id: "openai.chat_completions", labelKey: "app.admin.dashboard.model.hub.protocol.openaiChatCompletions" },
    { id: "openai.responses", labelKey: "app.admin.dashboard.model.hub.protocol.openaiResponses" },
    { id: "anthropic.messages", labelKey: "app.admin.dashboard.model.hub.protocol.anthropicMessages" },
    { id: "gemini.generate_content", labelKey: "app.admin.dashboard.model.hub.protocol.geminiGenerateContent" },
];

const PROVIDER_CHANNEL_PRESETS: Array<{
    id: string;
    labelKey: string;
    apiStandard: "openai" | "anthropic" | "gemini" | "comfyui";
    wireProtocols: Exclude<ModelWireProtocol, "">[];
    defaultWireProtocol: ModelWireProtocol;
}> = [
    { id: "openai", labelKey: "app.admin.dashboard.model.hub.channel.openai", apiStandard: "openai", wireProtocols: ["openai.chat_completions", "openai.responses"], defaultWireProtocol: "openai.chat_completions" },
    { id: "anthropic", labelKey: "app.admin.dashboard.model.hub.channel.anthropic", apiStandard: "anthropic", wireProtocols: ["anthropic.messages"], defaultWireProtocol: "anthropic.messages" },
    { id: "gemini", labelKey: "app.admin.dashboard.model.hub.channel.gemini", apiStandard: "gemini", wireProtocols: ["gemini.generate_content"], defaultWireProtocol: "gemini.generate_content" },
    { id: "comfyui", labelKey: "app.admin.dashboard.model.hub.channel.comfyui", apiStandard: "comfyui", wireProtocols: [], defaultWireProtocol: "" },
];

function createProviderChannel(apiStandard: string, baseUrl = "", id?: string): ProviderChannel {
    const preset = PROVIDER_CHANNEL_PRESETS.find((item) => item.apiStandard === apiStandard) || PROVIDER_CHANNEL_PRESETS[0];
    return {
        id: id || preset.id,
        label: preset.id,
        apiStandard: preset.apiStandard,
        baseUrl,
        apiVersion: "",
        wireProtocols: [...preset.wireProtocols],
        defaultWireProtocol: preset.defaultWireProtocol,
        source: "configured",
    };
}

function editableProviderChannels(provider: AIProvider | null, apiStandard: string, baseUrl: string): ProviderChannel[] {
    const configured = (provider?.channels || []).filter((channel) => channel.source !== "legacy_projection");
    if (configured.length > 0) return configured.map((channel) => ({ ...channel, wireProtocols: [...channel.wireProtocols] }));
    return [createProviderChannel(apiStandard, baseUrl, "default")];
}

const CUSTOM_PROVIDER_CAPABILITIES: Array<{ id: CustomProviderCapability; labelKey: string }> = [
    { id: "text", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityText" },
    { id: "vision", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVision" },
    { id: "image", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityImage" },
    { id: "video", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVideo" },
    { id: "voice", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVoice" },
    { id: "music", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityMusic" },
    { id: "model3d", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityModel3d" },
];
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

type ModelHubBootstrapPayload = {
    providers?: AIProvider[];
    models?: AIModel[];
    hubEnvelope?: ConfigRegistryEnvelope<ModelHubPayload> | null;
    defaultModel?: { modelRef?: string | null; modelId?: string | null; value?: string | null };
    catalog?: { providers?: CatalogProvider[] };
    audioConfig?: unknown;
};
const MODEL_HUB_BOOTSTRAP_URL = "/api/model-hub/bootstrap";
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
    availability?: "available" | "confirmed" | "pending_activation" | string;
};
type TtsVoiceCapabilities = {
    clone?: boolean;
    design?: boolean;
    list?: boolean;
    delete?: boolean;
    preview?: boolean;
    commit?: boolean;
};
type TtsVoiceAssetPolicy = {
    assetScope?: "durable_remote" | "provider_slot" | "ephemeral_request" | "qualification_only" | string;
    inventorySource?: "remote" | "local_projection" | "none" | string;
    designFlow?: "direct" | "ephemeral" | "preview_then_commit" | "qualification_only" | string;
    eligibilityStatus?: "available" | "eligible" | "requires_approval" | string;
    consentRequired?: boolean;
    docsUrl?: string;
    applicationUrl?: string;
};
type TtsVoiceDesignCandidate = {
    generatedVoiceId: string;
    previewAudio: string;
};
type TtsVoiceTextConstraint = {
    required?: boolean;
    minChars?: number;
    maxChars?: number | null;
};
type TtsVoiceDesignConstraints = {
    prompt?: TtsVoiceTextConstraint;
    previewText?: TtsVoiceTextConstraint;
    voiceId?: TtsVoiceTextConstraint & {
        role?: "none" | "custom_id" | "prefix" | "provider_slot" | string;
        format?: string;
    };
};
type TtsVoiceProviderInfo = {
    modelRef?: string;
    provider?: string;
    capabilities?: TtsVoiceCapabilities;
    assetPolicy?: TtsVoiceAssetPolicy;
    designConstraints?: TtsVoiceDesignConstraints;
    credentialStatus?: "configured" | "missing";
    sampleLimits?: {
        minDurationSeconds?: number;
        maxDurationSeconds?: number;
        maxBytes?: number;
        formats?: string[];
    };
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

function previewModelsUrl(provider?: CatalogProvider | null): string {
    if (!provider?.baseUrl) return "";
    if (provider.modelsUrl) return provider.modelsUrl;
    const path = provider.modelsPath || "/models";
    return `${provider.baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

function catalogProviderChannels(provider?: CatalogProvider | null): ProviderChannel[] {
    if (!provider) return [];
    if (provider.channels?.length) return provider.channels;
    const primary = createProviderChannel(provider.apiStandard || "openai", provider.baseUrl || "", "default");
    const channels = [primary];
    if (provider.anthropicCompatible?.baseUrl) {
        channels.push(createProviderChannel("anthropic", provider.anthropicCompatible.baseUrl, "anthropic"));
    }
    return channels;
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
            requestPath: relativePath,
            routeSource: "provider_catalog",
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

function preserveModelOrder(current: AIModel[], incoming: AIModel[]): AIModel[] {
    if (!current.length || !incoming.length) return incoming;
    const identity = (model: AIModel) => model.modelRef || `${model.providerId}:${model.id}`;
    const incomingByIdentity = new Map(incoming.map((model) => [identity(model), model]));
    const ordered = current
        .map((model) => incomingByIdentity.get(identity(model)))
        .filter((model): model is AIModel => Boolean(model));
    const known = new Set(ordered.map(identity));
    for (const model of incoming) {
        if (!known.has(identity(model))) ordered.push(model);
    }
    return ordered;
}

function providerMatchesPurpose(provider: CatalogProvider, purpose: CatalogPurpose) {
    const authType = provider.auth?.type;
    if (authType === "oauth_file") return purpose === "chat";
    const mediaModality = String(provider.mediaModality || "").toLowerCase();
    const providerKind = String(provider.providerKind || "").toLowerCase();
    const apiStandard = String(provider.apiStandard || "").toLowerCase();
    const declaredCapabilities = new Set((provider.declaredCapabilities || []).map((item) => String(item).toLowerCase()));
    if (provider.isCustom && declaredCapabilities.size > 0) {
        return purpose === "chat"
            ? declaredCapabilities.has("text") || declaredCapabilities.has("vision")
            : declaredCapabilities.has(purpose);
    }
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
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-muted text-[10px] font-semibold text-muted-foreground dark:bg-card/10 dark:text-slate-300">
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
function voiceManagerErrorText(payload: Record<string, unknown>, fallback: string): string {
    const message = extractErrorText(payload.error, fallback);
    const providerCode = typeof payload.providerCode === "string" ? payload.providerCode.trim() : "";
    const traceId = typeof payload.traceId === "string" ? payload.traceId.trim() : "";
    return [message, providerCode ? `code ${providerCode}` : "", traceId ? `trace ${traceId}` : ""]
        .filter(Boolean)
        .join(" · ");
}
async function readJsonErrorMessage(response: Response, fallback: string) {
    const data = await response.json().catch(() => null);
    const detail = extractErrorText(data?.detail, "") || extractErrorText(data?.error, "");
    return detail || fallback;
}
export default function ModelHubPage() {
    const { toast } = useToast();
    const t = useT();
    const cachedBootstrap = peekAdminJsonCache<ModelHubBootstrapPayload>(MODEL_HUB_BOOTSTRAP_URL);
    const [providers, setProviders] = useState<AIProvider[]>(() => Array.isArray(cachedBootstrap?.providers) ? cachedBootstrap.providers : []);
    const [models, setModels] = useState<AIModel[]>(() => Array.isArray(cachedBootstrap?.models) ? cachedBootstrap.models : []);
    const [hubEnvelope, setHubEnvelope] = useState<ConfigRegistryEnvelope<ModelHubPayload> | null>(() => cachedBootstrap?.hubEnvelope || null);
    const [isLoading, setIsLoading] = useState(() => !cachedBootstrap);
    const [hasLoadedAudioConfig, setHasLoadedAudioConfig] = useState(() => Boolean(cachedBootstrap));
    const [activeTab, setActiveTab] = useState("all");
    const [isProviderDialogOpen, setIsProviderDialogOpen] = useState(false);
    const [isModelDialogOpen, setIsModelDialogOpen] = useState(false);
    const [editingProvider, setEditingProvider] = useState<AIProvider | null>(null);
    const [editingModel, setEditingModel] = useState<AIModel | null>(null);
    const [providerType, setProviderType] = useState<AIProvider["type"]>("API");
    const [providerCredentialMode, setProviderCredentialMode] = useState<"apiKey" | "oauthFile">("apiKey");
    const [providerApiStandard, setProviderApiStandard] = useState<"openai" | "anthropic" | "gemini" | "comfyui">("openai");
    const [providerBaseUrl, setProviderBaseUrl] = useState("");
    const [providerChannels, setProviderChannels] = useState<ProviderChannel[]>([createProviderChannel("openai", "", "default")]);
    const [providerDefaultChannelId, setProviderDefaultChannelId] = useState("default");
    const [providerApiKey, setProviderApiKey] = useState("");
    const [providerOauthPath, setProviderOauthPath] = useState("");
    const [platformLoginPreset, setPlatformLoginPreset] = useState<PlatformLoginPreset>("codex");
    const [localBackendPreset, setLocalBackendPreset] = useState<LocalBackendPreset>("ollama");
    const [modelType, setModelType] = useState("TEXT");
    const [mediaCapabilityModes, setMediaCapabilityModes] = useState<string[]>([]);
    const [modelProviderId, setModelProviderId] = useState("");
    const [modelChannelId, setModelChannelId] = useState("");
    const [modelWireProtocol, setModelWireProtocol] = useState<ModelWireProtocol>("");
    const [comfyWorkflow, setComfyWorkflow] = useState<ComfyWorkflowDraft>(EMPTY_COMFY_WORKFLOW);
    const [rerankApiFlavor, setRerankApiFlavor] = useState("generic");
    const [connectionStatusMap, setConnectionStatusMap] = useState<Record<string, ModelConnectionStatus>>({});
    const [reasoningRepairStatusMap, setReasoningRepairStatusMap] = useState<Record<string, ModelReasoningRepairStatus>>({});
    const [defaultModelRef, setDefaultModelRef] = useState<string | null>(() => {
        const value = cachedBootstrap?.defaultModel || {};
        return value.modelRef || value.modelId || value.value || null;
    });
    const [catalogProviders, setCatalogProviders] = useState<CatalogProvider[]>(() => Array.isArray(cachedBootstrap?.catalog?.providers) ? cachedBootstrap.catalog.providers : []);
    const [catalogPurpose, setCatalogPurpose] = useState<CatalogPurpose>("chat");
    const [catalogRuntimeProtocol, setCatalogRuntimeProtocol] = useState<CatalogRuntimeProtocol>("default");
    const [selectedCatalogProviderId, setSelectedCatalogProviderId] = useState("");
    const [catalogApiKey, setCatalogApiKey] = useState("");
    const [catalogVoiceAppId, setCatalogVoiceAppId] = useState("");
    const [catalogVoiceResourceId, setCatalogVoiceResourceId] = useState("");
    const [catalogProbeModels, setCatalogProbeModels] = useState<CatalogModel[]>([]);
    const [selectedCatalogModelId, setSelectedCatalogModelId] = useState("");
    const [catalogModelFilter, setCatalogModelFilter] = useState("");
    const [customProviderName, setCustomProviderName] = useState("");
    const [customProviderBaseUrl, setCustomProviderBaseUrl] = useState("");
    const [customProviderChannels, setCustomProviderChannels] = useState<ProviderChannel[]>([createProviderChannel("openai")]);
    const [customProviderDefaultChannelId, setCustomProviderDefaultChannelId] = useState("openai");
    const [customProviderCapabilities, setCustomProviderCapabilities] = useState<CustomProviderCapability[]>(["text", "vision"]);
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
    const pendingCatalogProviderIdRef = useRef("");
    const [audioConfig, setAudioConfig] = useState<AudioRuntimeConfig>(() => mergeAudioConfig(cachedBootstrap?.audioConfig || null));
    const [isAudioSaving, setIsAudioSaving] = useState(false);
    const [customTtsRemoteVoices, setCustomTtsRemoteVoices] = useState<AudioVoiceOption[]>([]);
    const [isCustomTtsVoiceLoading, setIsCustomTtsVoiceLoading] = useState(false);
    const [modelRefTtsVoices, setModelRefTtsVoices] = useState<AudioVoiceOption[]>([]);
    const [modelRefTtsVoiceInfo, setModelRefTtsVoiceInfo] = useState<TtsVoiceProviderInfo | null>(null);
    const [modelRefTtsVoiceListState, setModelRefTtsVoiceListState] = useState<"idle" | "loading" | "loaded" | "error">("idle");
    const [isModelRefTtsVoiceLoading, setIsModelRefTtsVoiceLoading] = useState(false);
    const [deletingModelRefTtsVoiceId, setDeletingModelRefTtsVoiceId] = useState<string | null>(null);
    const [ttsCloneFile, setTtsCloneFile] = useState<File | null>(null);
    const [ttsCloneVoiceId, setTtsCloneVoiceId] = useState("");
    const [ttsClonePreviewText, setTtsClonePreviewText] = useState("");
    const [isTtsCloning, setIsTtsCloning] = useState(false);
    const [isTtsClonePanelOpen, setIsTtsClonePanelOpen] = useState(false);
    const [isTtsDesignPanelOpen, setIsTtsDesignPanelOpen] = useState(false);
    const [ttsDesignPrompt, setTtsDesignPrompt] = useState("");
    const [ttsDesignPreviewText, setTtsDesignPreviewText] = useState("");
    const [ttsDesignVoiceId, setTtsDesignVoiceId] = useState("");
    const [ttsDesignVoiceName, setTtsDesignVoiceName] = useState("");
    const [ttsDesignCandidates, setTtsDesignCandidates] = useState<TtsVoiceDesignCandidate[]>([]);
    const [ttsVoiceOperationPreviewUrl, setTtsVoiceOperationPreviewUrl] = useState("");
    const [isTtsDesigning, setIsTtsDesigning] = useState(false);
    const [committingTtsDesignId, setCommittingTtsDesignId] = useState<string | null>(null);
    const [ttsPreviewText, setTtsPreviewText] = useState("");
    const [ttsPreviewUrl, setTtsPreviewUrl] = useState("");
    const [isTtsPreviewing, setIsTtsPreviewing] = useState(false);
    const selectedTtsModelRefRef = useRef("");
    const fetchData = async (force = false, keepModelOrder = false) => {
        if (!peekAdminJsonCache(MODEL_HUB_BOOTSTRAP_URL)) setIsLoading(true);
        try {
            const payload = await fetchAdminJson<ModelHubBootstrapPayload>(MODEL_HUB_BOOTSTRAP_URL, { force, ttlMs: 30_000 });
            setProviders(Array.isArray(payload.providers) ? payload.providers : []);
            const nextModels = Array.isArray(payload.models) ? payload.models : [];
            setModels((current) => keepModelOrder ? preserveModelOrder(current, nextModels) : nextModels);
            setHubEnvelope(payload.hubEnvelope || null);
            setAudioConfig(mergeAudioConfig(payload.audioConfig || null));
            setHasLoadedAudioConfig(true);
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
    const selectedModelProvider = useMemo(
        () => providers.find((provider) => provider.id === modelProviderId) || null,
        [modelProviderId, providers],
    );
    const selectedModelChannel = useMemo(
        () => selectedModelProvider?.channels?.find((channel) => channel.id === modelChannelId)
            || selectedModelProvider?.channels?.find((channel) => channel.id === selectedModelProvider.defaultChannelId)
            || selectedModelProvider?.channels?.[0]
            || null,
        [modelChannelId, selectedModelProvider],
    );
    const selectedCustomChannel = useMemo(
        () => customProviderChannels.find((channel) => channel.id === customProviderDefaultChannelId)
            || customProviderChannels[0],
        [customProviderChannels, customProviderDefaultChannelId],
    );
    const catalogPurposeConfig = useMemo(() => getCatalogPurposeConfig(catalogPurpose), [catalogPurpose]);
    const selectedCatalogChannels = useMemo(
        () => catalogProviderChannels(selectedCatalogProvider),
        [selectedCatalogProvider],
    );
    const selectedCatalogRuntime = useMemo(() => {
        const requestedChannelId = catalogRuntimeProtocol === "default"
            ? selectedCatalogProvider?.defaultChannelId || selectedCatalogChannels[0]?.id
            : catalogRuntimeProtocol;
        const channel = selectedCatalogChannels.find((item) => item.id === requestedChannelId) || selectedCatalogChannels[0];
        return {
            channelId: channel?.id || "",
            apiStandard: channel?.apiStandard || selectedCatalogProvider?.apiStandard || (catalogPurpose === "workflow" ? "comfyui" : "openai"),
            baseUrl: channel?.baseUrl || selectedCatalogProvider?.baseUrl || "",
            label: channel?.label || selectedCatalogProvider?.apiStandard || "default",
            wireProtocol: channel?.defaultWireProtocol || "",
        };
    }, [catalogPurpose, catalogRuntimeProtocol, selectedCatalogChannels, selectedCatalogProvider]);
    const catalogPurposeLabel = t(catalogPurposeConfig.labelKey);
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
    selectedTtsModelRefRef.current = selectedTtsModelRef;
    const modelRefTtsVoiceCapabilities = modelRefTtsVoiceInfo?.capabilities || {};
    const modelRefTtsVoiceAssetPolicy = modelRefTtsVoiceInfo?.assetPolicy;
    const modelRefTtsVoiceDesignConstraints = modelRefTtsVoiceInfo?.designConstraints;
    const isQualificationOnlyVoice = modelRefTtsVoiceAssetPolicy?.assetScope === "qualification_only";
    const isEphemeralReferenceVoice = modelRefTtsVoiceAssetPolicy?.assetScope === "ephemeral_request";
    const isProviderSlotVoice = modelRefTtsVoiceAssetPolicy?.assetScope === "provider_slot";
    const ttsVoiceDesignIdRole = modelRefTtsVoiceDesignConstraints?.voiceId?.role || "none";
    const isTtsVoiceDesignIdVisible = ttsVoiceDesignIdRole !== "none";
    const isTtsVoiceDesignIdRequired = modelRefTtsVoiceDesignConstraints?.voiceId?.required === true;
    const ttsDesignPromptMinChars = modelRefTtsVoiceDesignConstraints?.prompt?.minChars ?? 1;
    const ttsDesignPromptMaxChars = modelRefTtsVoiceDesignConstraints?.prompt?.maxChars ?? undefined;
    const ttsDesignPreviewMinChars = modelRefTtsVoiceDesignConstraints?.previewText?.minChars ?? 1;
    const ttsDesignPreviewMaxChars = modelRefTtsVoiceDesignConstraints?.previewText?.maxChars ?? undefined;
    const ttsDesignVoiceIdMinChars = modelRefTtsVoiceDesignConstraints?.voiceId?.minChars ?? undefined;
    const ttsDesignVoiceIdMaxChars = modelRefTtsVoiceDesignConstraints?.voiceId?.maxChars ?? undefined;
    const ttsDesignVoiceIdFormat = modelRefTtsVoiceDesignConstraints?.voiceId?.format || "";
    const ttsDesignVoiceIdPlaceholderKey = ttsVoiceDesignIdRole === "provider_slot"
        ? "app.admin.dashboard.model.hub.audio.voiceSlotPlaceholder"
        : ttsVoiceDesignIdRole === "prefix"
            ? "app.admin.dashboard.model.hub.audio.voicePrefixPlaceholder"
            : "app.admin.dashboard.model.hub.audio.voiceCustomIdPlaceholder";
    const isTtsDesignVoiceIdFormatValid = !ttsDesignVoiceId.trim()
        || (ttsDesignVoiceIdFormat === "ascii_identifier"
            ? /^[A-Za-z][A-Za-z0-9_-]*[A-Za-z0-9]$/.test(ttsDesignVoiceId.trim())
            : ttsDesignVoiceIdFormat === "ascii_alphanumeric"
                ? /^[A-Za-z0-9]+$/.test(ttsDesignVoiceId.trim())
                : true);
    const isTtsDesignInputValid = ttsDesignPrompt.trim().length >= ttsDesignPromptMinChars
        && (ttsDesignPromptMaxChars === undefined || ttsDesignPrompt.trim().length <= ttsDesignPromptMaxChars)
        && ttsDesignPreviewText.trim().length >= ttsDesignPreviewMinChars
        && (ttsDesignPreviewMaxChars === undefined || ttsDesignPreviewText.trim().length <= ttsDesignPreviewMaxChars)
        && (!isTtsVoiceDesignIdRequired || Boolean(ttsDesignVoiceId.trim()))
        && (!ttsDesignVoiceId.trim() || ttsDesignVoiceIdMinChars === undefined || ttsDesignVoiceId.trim().length >= ttsDesignVoiceIdMinChars)
        && (!ttsDesignVoiceId.trim() || ttsDesignVoiceIdMaxChars === undefined || ttsDesignVoiceId.trim().length <= ttsDesignVoiceIdMaxChars)
        && isTtsDesignVoiceIdFormatValid;
    const isModelRefVoiceCredentialMissing = modelRefTtsVoiceInfo?.credentialStatus === "missing";
    const isManagedModelRefTtsVoice = audioConfig.tts.active_provider === "model_ref"
        && modelRefTtsVoiceInfo?.modelRef === selectedTtsModelRef
        && Boolean(modelRefTtsVoiceInfo?.provider)
        && (Boolean(modelRefTtsVoiceAssetPolicy) || Object.values(modelRefTtsVoiceCapabilities).some(Boolean));
    const selectedModelRefTtsVoice = modelRefTtsVoices.find((voice) => voice.value === audioConfig.tts.model_ref?.voice);
    const isConfiguredModelRefTtsVoiceUnavailable = modelRefTtsVoiceCapabilities.list === true
        && modelRefTtsVoiceListState === "loaded"
        && Boolean(audioConfig.tts.model_ref?.voice)
        && !selectedModelRefTtsVoice;
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
        if (isLoading || isCatalogBusy || pendingCatalogProviderIdRef.current) return;
        if (apiCatalogProviders.some((item) => item.id === selectedCatalogProviderId) || selectedCatalogProviderId === "__custom__") return;
        setSelectedCatalogProviderId(apiCatalogProviders[0]?.id || "__custom__");
        setCatalogApiKey("");
        setCatalogVoiceAppId("");
        setCatalogVoiceResourceId("");
        setCatalogProbeModels([]);
        setSelectedCatalogModelId("");
        setCatalogModelFilter("");
        setProbedCatalogProviderId("");
        setCatalogProbeStatus(null);
        setManualModelEntryEnabled(false);
        setCatalogRuntimeProtocol("default");
    }, [apiCatalogProviders, isCatalogBusy, isLoading, selectedCatalogProviderId]);
    useEffect(() => {
        setModelRefTtsVoices([]);
        setModelRefTtsVoiceInfo(null);
        setModelRefTtsVoiceListState("idle");
        setTtsCloneFile(null);
        setTtsCloneVoiceId("");
        setTtsClonePreviewText("");
        setIsTtsClonePanelOpen(false);
        setIsTtsDesignPanelOpen(false);
        setTtsDesignPrompt("");
        setTtsDesignPreviewText("");
        setTtsDesignVoiceId("");
        setTtsDesignVoiceName("");
        setTtsDesignCandidates([]);
        setTtsVoiceOperationPreviewUrl("");
    }, [selectedTtsModelRef]);
    useEffect(() => {
        if (audioConfig.tts.active_provider !== "model_ref" || !selectedTtsModelRef) return;
        const controller = new AbortController();
        setIsModelRefTtsVoiceLoading(true);
        setModelRefTtsVoiceListState("loading");
        void (async () => {
            try {
                const capabilitiesResponse = await fetch("/api/audio/model-ref-voices", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "capabilities", modelRef: selectedTtsModelRef }),
                    signal: controller.signal,
                });
                const payload = await capabilitiesResponse.json().catch(() => ({}));
                if (!capabilitiesResponse.ok || controller.signal.aborted) {
                    if (!controller.signal.aborted) setModelRefTtsVoiceListState("error");
                    return;
                }
                setModelRefTtsVoiceInfo({
                    modelRef: selectedTtsModelRef,
                    provider: typeof payload.provider === "string" ? payload.provider : undefined,
                    capabilities: payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : undefined,
                    assetPolicy: payload.assetPolicy && typeof payload.assetPolicy === "object" ? payload.assetPolicy : undefined,
                    designConstraints: payload.designConstraints && typeof payload.designConstraints === "object" ? payload.designConstraints : undefined,
                    credentialStatus: payload.credentialStatus === "configured" ? "configured" : "missing",
                    sampleLimits: payload.sampleLimits && typeof payload.sampleLimits === "object" ? payload.sampleLimits : undefined,
                });
                if (payload.capabilities?.list !== true) {
                    setModelRefTtsVoiceListState("loaded");
                    return;
                }
                const listResponse = await fetch("/api/audio/model-ref-voices", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "list", modelRef: selectedTtsModelRef }),
                    signal: controller.signal,
                });
                const listPayload = await listResponse.json().catch(() => ({}));
                if (!listResponse.ok || controller.signal.aborted) {
                    if (!controller.signal.aborted) setModelRefTtsVoiceListState("error");
                    return;
                }
                setModelRefTtsVoices(Array.isArray(listPayload.voices) ? listPayload.voices : []);
                setModelRefTtsVoiceListState("loaded");
            } catch {
                if (!controller.signal.aborted) setModelRefTtsVoiceListState("error");
            } finally {
                if (!controller.signal.aborted) setIsModelRefTtsVoiceLoading(false);
            }
        })();
        return () => controller.abort();
    }, [audioConfig.tts.active_provider, selectedTtsModelRef]);
    useEffect(() => () => {
        if (ttsPreviewUrl) URL.revokeObjectURL(ttsPreviewUrl);
    }, [ttsPreviewUrl]);
    const filteredModels = models.filter((model) => modelMatchesTab(model, activeTab));
    const handleSaveProvider = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        const payload: Record<string, unknown> = Object.fromEntries(formData.entries());
        if (providerType === "API") {
            const normalizedChannels = providerChannels.map((channel) => ({
                ...channel,
                id: channel.id.trim().toLowerCase(),
                label: channel.label.trim() || channel.id.trim(),
                baseUrl: channel.baseUrl.trim().replace(/\/+$/, ""),
                apiVersion: channel.apiVersion.trim().replace(/^\/+|\/+$/g, ""),
            }));
            const invalidAnthropicChannel = normalizedChannels.find(
                (channel) => channel.apiStandard === "anthropic" && /\/v1$/i.test(channel.baseUrl),
            );
            if (invalidAnthropicChannel) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.page.kd2b2caac"),
                    description: t("app.admin.dashboard.model.hub.channel.anthropicBaseUrlError"),
                });
                return;
            }
            const defaultChannel = normalizedChannels.find((channel) => channel.id === providerDefaultChannelId) || normalizedChannels[0];
            payload.channels = normalizedChannels;
            payload.defaultChannelId = defaultChannel?.id || "";
            payload.baseUrl = defaultChannel?.baseUrl || providerBaseUrl;
            payload.apiStandard = defaultChannel?.apiStandard || providerApiStandard;
        }
        const url = editingProvider ? `/api/providers/${editingProvider.id}` : "/api/providers";
        const method = editingProvider ? "PUT" : "POST";
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
        setIsProviderDialogOpen(false);
        setEditingProvider(null);
        await fetchData(true);
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
        const payload: Record<string, unknown> = {
            ...Object.fromEntries(formData.entries()),
            mediaLimits: editingModel?.mediaLimits || undefined,
            endpointBinding: editingModel?.endpointBinding || undefined,
            channelId: modelChannelId,
        };
        if (modelType === "WORKFLOW") {
            let prompt: Record<string, unknown>;
            try {
                const parsed = JSON.parse(comfyWorkflow.promptJson || "null");
                if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("invalid");
                prompt = parsed as Record<string, unknown>;
            } catch {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.comfy.workflowInvalidTitle"),
                    description: t("app.admin.dashboard.model.hub.comfy.workflowInvalid"),
                });
                return;
            }
            const requiredBindings = [
                comfyWorkflow.imageNodeId,
                comfyWorkflow.imageInputName,
                comfyWorkflow.videoNodeId,
                comfyWorkflow.videoInputName,
                comfyWorkflow.outputNodeId,
                comfyWorkflow.outputField,
            ];
            if (requiredBindings.some((value) => !value.trim())) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.comfy.workflowInvalidTitle"),
                    description: t("app.admin.dashboard.model.hub.comfy.bindingsRequired"),
                });
                return;
            }
            payload.mediaLimits = {
                ...(editingModel?.mediaLimits || {}),
                comfyuiWorkflow: {
                    schema: "v8.comfyui.workflow.v1",
                    operationKind: "video.action_transfer",
                    prompt,
                    bindings: {
                        image: { nodeId: comfyWorkflow.imageNodeId.trim(), inputName: comfyWorkflow.imageInputName.trim() },
                        video: { nodeId: comfyWorkflow.videoNodeId.trim(), inputName: comfyWorkflow.videoInputName.trim() },
                    },
                    output: { nodeId: comfyWorkflow.outputNodeId.trim(), field: comfyWorkflow.outputField.trim(), index: 0 },
                },
            };
        }
        if (["TEXT", "MULTIMODAL", "VISION", "CHAT"].includes(modelType)) {
            payload.wireProtocol = modelWireProtocol;
        }
        if (getMediaCapabilityOptions(modelType).length > 0) {
            payload.capabilityModes = mediaCapabilityModes;
        }
        for (const key of ["contextWindow", "maxTokens"]) {
            if (payload[key] === "") payload[key] = null;
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
        await fetchData(true);
    };
    const handleSetReasoningLevel = async (model: AIModel, controlMeta: ControlPlaneModel | null, level: string) => {
        const supportsNoThink = Boolean(controlMeta?.thinkingControl?.supportsNoThink);
        const disabled = supportsNoThink && level === "none";
        const effortProfileDriven = Boolean(controlMeta?.reasoningEffortControl?.profileId);
        const reasoningEffortControl = effortProfileDriven
            ? {
                supportsReasoningEffort: true,
                selectedLevel: disabled ? "auto" : level,
                source: "manual_selection",
            }
            : {
                ...(model.reasoningEffortControl || {}),
                ...(controlMeta?.reasoningEffortControl || {}),
                supportsReasoningEffort: Boolean(controlMeta?.reasoningEffortControl?.supportsReasoningEffort),
                selectedLevel: disabled ? "auto" : level,
                source: "manual_selection",
            };
        const noThinkProfileDriven = Boolean(controlMeta?.thinkingControl?.profileId);
        const thinkingControl = supportsNoThink
            ? noThinkProfileDriven
                ? { disabled, source: "manual_selection" }
                : {
                    ...(model.thinkingControl || {}),
                    ...(controlMeta?.thinkingControl || {}),
                    supportsNoThink: true,
                    disabled,
                    source: "manual_selection",
                }
            : model.thinkingControl || undefined;
        const response = await fetch(`/api/models/${encodeURIComponent(model.id)}?providerId=${encodeURIComponent(model.providerId)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                providerId: model.providerId,
                modelId: model.modelId,
                type: model.type || controlMeta?.type || "TEXT",
                contextWindow: model.contextWindow ?? controlMeta?.contextWindow ?? "",
                maxTokens: model.maxTokens ?? controlMeta?.maxTokens ?? "",
                rerankApiFlavor: model.rerankApiFlavor || "",
                reasoningEffortControl,
                thinkingControl,
            }),
        });
        if (!response.ok) {
            const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.thinkingSaveFailed"));
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.page.thinkingSaveFailed"), description: errorMessage });
            return;
        }
        toast({
            title: t("app.admin.dashboard.model.hub.page.reasoningEffortSaved"),
            description: `${model.modelId} · ${level}`,
        });
        await fetchData(true, true);
    };
    const handleToggleProviderHostedTools = async (model: AIModel, controlMeta: ControlPlaneModel | null, enabled: boolean) => {
        const endpointBinding = {
            ...(model.endpointBinding || {}),
            ...(controlMeta?.endpointBinding || {}),
            providerHostedTools: {
                enabled,
                tools: ["web_search"],
                source: "manual",
            },
        };
        const response = await fetch(`/api/models/${encodeURIComponent(model.id)}?providerId=${encodeURIComponent(model.providerId)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                providerId: model.providerId,
                modelId: model.modelId,
                type: model.type || controlMeta?.type || "TEXT",
                contextWindow: model.contextWindow ?? controlMeta?.contextWindow ?? "",
                maxTokens: model.maxTokens ?? controlMeta?.maxTokens ?? "",
                rerankApiFlavor: model.rerankApiFlavor || "",
                endpointBinding,
            }),
        });
        if (!response.ok) {
            const errorMessage = await readJsonErrorMessage(response, t("app.admin.dashboard.model.hub.page.providerHostedToolsSaveFailed"));
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.page.providerHostedToolsSaveFailed"),
                description: errorMessage,
            });
            return;
        }
        toast({
            title: enabled
                ? t("app.admin.dashboard.model.hub.page.providerHostedToolsEnabled")
                : t("app.admin.dashboard.model.hub.page.providerHostedToolsDisabled"),
            description: model.modelId,
        });
        await fetchData(true);
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
            await fetchData(true);
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
            await fetchData(true);
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
        await fetchData(true);
    };
    const handleProbeCatalogProvider = async () => {
        if (!selectedCatalogProviderId) return;
        const isCustomProvider = selectedCatalogProviderId === "__custom__";
        const isMediaPurpose = catalogPurpose !== "chat";
        const baseUrl = isCustomProvider
            ? (selectedCustomChannel?.baseUrl || customProviderBaseUrl).trim()
            : (selectedCatalogProvider?.baseUrl || selectedCatalogRuntime.baseUrl || "");
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
                    apiStandard: isCustomProvider
                        ? selectedCustomChannel?.apiStandard || (catalogPurpose === "workflow" ? "comfyui" : "openai")
                        : selectedCatalogProvider?.apiStandard || selectedCatalogRuntime.apiStandard,
                    declaredCapabilities: isCustomProvider ? customProviderCapabilities : [],
                    channels: isCustomProvider ? customProviderChannels : selectedCatalogChannels,
                    defaultChannelId: isCustomProvider ? customProviderDefaultChannelId : selectedCatalogRuntime.channelId,
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
                const persistedProvider = data.provider && typeof data.provider === "object"
                    ? data.provider as CatalogProvider
                    : null;
                pendingCatalogProviderIdRef.current = providerId;
                if (persistedProvider?.id) {
                    setCatalogProviders((current) => [
                        persistedProvider,
                        ...current.filter((item) => item.id !== persistedProvider.id),
                    ]);
                }
                setSelectedCatalogProviderId(providerId);
                await fetchData(true);
                pendingCatalogProviderIdRef.current = "";
            }
        }
        finally {
            pendingCatalogProviderIdRef.current = "";
            setIsCatalogBusy(false);
        }
    };
    const handleConnectCatalogModel = async (providerId: string, modelId: string, apiKey = "") => {
        if (!providerId || !modelId) return;
        setIsCatalogBusy(true);
        try {
            const provider = apiCatalogProviders.find((item) => item.id === providerId) || catalogProviders.find((item) => item.id === providerId);
            const selectedModel = catalogProbeModels.find((item) => (item.modelId || item.id) === modelId);
            const mediaLimits = selectedModel?.mediaLimits || {};
            const sourceProvider = catalogProviders.find((item) => item.id === selectedModel?.sourceProviderId);
            const operationKinds = Array.isArray(mediaLimits.operationKinds)
                ? mediaLimits.operationKinds.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
                : selectedModel?.operationKinds || [];
            const defaultOpenAiEndpointPath: Record<CatalogPurpose, string> = {
                chat: "",
                image: "images/generations",
                video: "videos/generations",
                voice: "audio/speech",
                music: "",
                workflow: "",
                model3d: "",
            };
            const defaultOperationKind: Record<CatalogPurpose, string> = {
                chat: "",
                image: "image.generate",
                video: "video.text_to_video",
                voice: "voice.tts",
                music: "music.generate",
                workflow: "",
                model3d: "model3d.generate",
            };
            const catalogEndpointPath = String(mediaLimits.endpointPath || mediaLimits.requestPath || "").replace(/^\/+|\/+$/g, "");
            const openAiCompatibleEndpointPath = String(selectedCatalogRuntime.apiStandard || "").toLowerCase().includes("openai")
                ? defaultOpenAiEndpointPath[catalogPurpose]
                : "";
            const endpointPath = catalogEndpointPath || openAiCompatibleEndpointPath;
            const explicitProviderModelId = String(mediaLimits.providerModelId || "").replace(/^\/+|\/+$/g, "");
            const providerModelId = explicitProviderModelId || (
                endpointPath && modelId.startsWith(`${endpointPath}/`)
                    ? modelId.slice(endpointPath.length + 1)
                    : modelId
            );
            const visibleRouteModelId = endpointPath && providerModelId
                ? `${endpointPath}/${providerModelId}`
                : modelId;
            const isCustomProvider = providerId === "__custom__" || selectedCatalogProviderId === "__custom__";
            const baseUrl = isCustomProvider
                ? (selectedCustomChannel?.baseUrl || customProviderBaseUrl).trim()
                : selectedCatalogRuntime.baseUrl || provider?.baseUrl || "";
            const isMediaPurpose = catalogPurpose !== "chat";
            const customAnthropicRuntime = isCustomProvider && catalogPurpose === "chat" && isXiaomiAnthropicBaseUrl(baseUrl);
            const response = await fetch("/api/models/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    providerId,
                    modelId: visibleRouteModelId,
                    apiKey,
                    baseUrl,
                    customProviderName: isCustomProvider ? customProviderName : "",
                    providerKind: isMediaPurpose ? "media_generation" : (provider?.providerKind || "chat"),
                    mediaModality: isMediaPurpose ? catalogPurposeConfig.modality : (provider?.mediaModality || ""),
                    apiStandard: isCustomProvider
                        ? (selectedCustomChannel?.apiStandard || (customAnthropicRuntime ? "anthropic" : catalogPurpose === "workflow" ? "comfyui" : "openai"))
                        : selectedCatalogRuntime.apiStandard,
                    modelType: getModelTypeForPurpose(catalogPurpose),
                    voiceAppId: requiresVolcengineVoiceConfig ? catalogVoiceAppId.trim() : "",
                    voiceResourceId: requiresVolcengineVoiceConfig ? catalogVoiceResourceId.trim() : "",
                    declaredCapabilities: isCustomProvider ? customProviderCapabilities : [],
                    endpointPath,
                    providerModelId,
                    operationKind: operationKinds[0] || defaultOperationKind[catalogPurpose],
                    adapter: String(mediaLimits.adapter || sourceProvider?.adapter || provider?.adapter || ""),
                    channels: isCustomProvider ? customProviderChannels : selectedCatalogChannels,
                    defaultChannelId: isCustomProvider ? customProviderDefaultChannelId : selectedCatalogRuntime.channelId,
                    channelId: isCustomProvider ? selectedCustomChannel?.id || "" : selectedCatalogRuntime.channelId,
                    wireProtocol: isCustomProvider ? selectedCustomChannel?.defaultWireProtocol || "" : selectedCatalogRuntime.wireProtocol,
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
            await fetchData(true);
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
        await fetchData(true);
    };
    const handleSaveAudioConfig = async () => {
        setIsAudioSaving(true);
        try {
            if (
                audioConfig.tts.active_provider === "model_ref"
                && selectedTtsModelRef
                && modelRefTtsVoiceCapabilities.list === true
            ) {
                const selectedVoice = String(audioConfig.tts.model_ref?.voice || "").trim();
                const listResponse = await fetch("/api/audio/model-ref-voices", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ action: "list", modelRef: selectedTtsModelRef }),
                });
                const listPayload = await listResponse.json().catch(() => ({}));
                if (!listResponse.ok) {
                    setModelRefTtsVoiceListState("error");
                    toast({
                        variant: "destructive",
                        title: t("app.admin.dashboard.model.hub.audio.voiceFetchFailed"),
                        description: voiceManagerErrorText(listPayload, t("app.admin.dashboard.model.hub.audio.voiceFetchFailed")),
                    });
                    return;
                }
                const confirmedVoices = Array.isArray(listPayload.voices) ? listPayload.voices : [];
                setModelRefTtsVoices(confirmedVoices);
                setModelRefTtsVoiceListState("loaded");
                if (!selectedVoice || !confirmedVoices.some((voice: AudioVoiceOption) => voice.value === selectedVoice)) {
                    toast({
                        variant: "destructive",
                        title: t("app.admin.dashboard.model.hub.audio.voiceUnavailableSaveBlocked"),
                        description: t("app.admin.dashboard.model.hub.audio.voiceUnavailable"),
                    });
                    return;
                }
            }
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
            const savedConfig = await response.json().catch(() => null);
            if (!savedConfig || typeof savedConfig !== "object" || !("stt" in savedConfig) || !("tts" in savedConfig)) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.saveFailed"),
                    description: t("app.admin.dashboard.model.hub.audio.invalidSaveResponse"),
                });
                return;
            }
            setAudioConfig(mergeAudioConfig(savedConfig));
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
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.voiceFetchFailed")),
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
    const handleFetchModelRefTtsVoices = async (notify = true): Promise<AudioVoiceOption[] | null> => {
        if (!selectedTtsModelRef) return null;
        const requestedModelRef = selectedTtsModelRef;
        setIsModelRefTtsVoiceLoading(true);
        setModelRefTtsVoiceListState("loading");
        try {
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "list", modelRef: selectedTtsModelRef }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (selectedTtsModelRefRef.current === requestedModelRef) setModelRefTtsVoiceListState("error");
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceFetchFailed"),
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.voiceFetchFailed")),
                });
                return null;
            }
            const voices = Array.isArray(payload.voices) ? payload.voices : [];
            if (selectedTtsModelRefRef.current !== requestedModelRef) return null;
            setModelRefTtsVoices(voices);
            setModelRefTtsVoiceListState("loaded");
            setModelRefTtsVoiceInfo({
                modelRef: selectedTtsModelRef,
                provider: typeof payload.provider === "string" ? payload.provider : undefined,
                capabilities: payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : undefined,
                assetPolicy: payload.assetPolicy && typeof payload.assetPolicy === "object" ? payload.assetPolicy : undefined,
                designConstraints: payload.designConstraints && typeof payload.designConstraints === "object" ? payload.designConstraints : undefined,
                credentialStatus: payload.credentialStatus === "configured" ? "configured" : "missing",
                sampleLimits: payload.sampleLimits && typeof payload.sampleLimits === "object" ? payload.sampleLimits : undefined,
            });
            if (notify) {
                toast({
                    title: voices.length > 0 ? t("app.admin.dashboard.model.hub.audio.voiceFetchSuccess") : t("app.admin.dashboard.model.hub.audio.voiceFetchEmpty"),
                });
            }
            return voices;
        } finally {
            if (selectedTtsModelRefRef.current === requestedModelRef) setIsModelRefTtsVoiceLoading(false);
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
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.voiceDeleteFailed")),
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
        if (
            !selectedTtsModelRef
            || !ttsCloneFile
            || (!isEphemeralReferenceVoice && !ttsCloneVoiceId.trim())
            || (isEphemeralReferenceVoice && !ttsClonePreviewText.trim())
        ) {
            toast({
                variant: "destructive",
                title: t(isEphemeralReferenceVoice
                    ? "app.admin.dashboard.model.hub.audio.referenceVoiceMissing"
                    : "app.admin.dashboard.model.hub.audio.voiceCloneMissing"),
            });
            return;
        }
        setIsTtsCloning(true);
        try {
            const formData = new FormData();
            formData.append("action", "clone_from_upload");
            formData.append("modelRef", selectedTtsModelRef);
            if (ttsCloneVoiceId.trim()) formData.append("voiceId", ttsCloneVoiceId.trim());
            if (ttsClonePreviewText.trim()) formData.append("previewText", ttsClonePreviewText.trim());
            formData.append("file", ttsCloneFile);
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                body: formData,
            });
            const payload = await response.json().catch(() => ({}));
            setModelRefTtsVoiceInfo({
                modelRef: selectedTtsModelRef,
                provider: typeof payload.provider === "string" ? payload.provider : modelRefTtsVoiceInfo?.provider,
                capabilities: payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : modelRefTtsVoiceInfo?.capabilities,
                assetPolicy: payload.assetPolicy && typeof payload.assetPolicy === "object" ? payload.assetPolicy : modelRefTtsVoiceInfo?.assetPolicy,
                designConstraints: payload.designConstraints && typeof payload.designConstraints === "object" ? payload.designConstraints : modelRefTtsVoiceInfo?.designConstraints,
                credentialStatus: payload.credentialStatus === "configured" ? "configured" : modelRefTtsVoiceInfo?.credentialStatus,
                sampleLimits: payload.sampleLimits && typeof payload.sampleLimits === "object" ? payload.sampleLimits : modelRefTtsVoiceInfo?.sampleLimits,
            });
            if (!response.ok || payload.ok === false) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceCloneFailed"),
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.voiceCloneFailed")),
                });
                return;
            }
            const operationPreview = String(payload.previewAudio || payload.previewAudioUrl || "");
            if (operationPreview) setTtsVoiceOperationPreviewUrl(operationPreview);
            if (payload.ephemeral === true || modelRefTtsVoiceAssetPolicy?.assetScope === "ephemeral_request") {
                setTtsCloneFile(null);
                toast({ title: t("app.admin.dashboard.model.hub.audio.referenceVoiceReady") });
                return;
            }
            const clonedVoiceId = String(payload.voiceId || ttsCloneVoiceId.trim());
            if (!clonedVoiceId) {
                toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.voiceCloneFailed") });
                return;
            }
            setModelRefTtsVoices((current) => [
                {
                    value: clonedVoiceId,
                    label: clonedVoiceId,
                    group: "custom",
                    deletable: modelRefTtsVoiceCapabilities.delete === true,
                    source: modelRefTtsVoiceAssetPolicy?.inventorySource === "remote" ? "remote" : "local_projection",
                    availability: String(payload.availability || "pending_activation"),
                },
                ...current.filter((voice) => voice.value !== clonedVoiceId),
            ]);
            setModelRefTtsVoiceListState("loaded");
            setTtsModelRefValue("voice", clonedVoiceId);
            setTtsCloneFile(null);
            setTtsCloneVoiceId("");
            setTtsClonePreviewText("");
            setIsTtsClonePanelOpen(false);
            toast({ title: t("app.admin.dashboard.model.hub.audio.voiceCloneSuccess"), description: clonedVoiceId });
            if (modelRefTtsVoiceCapabilities.list === true) await handleFetchModelRefTtsVoices(false);
        } finally {
            setIsTtsCloning(false);
        }
    };
    const handleDesignModelRefTtsVoice = async () => {
        if (!selectedTtsModelRef || !ttsDesignPrompt.trim() || !ttsDesignPreviewText.trim()) {
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.voiceDesignMissing") });
            return;
        }
        if (isTtsVoiceDesignIdRequired && !ttsDesignVoiceId.trim()) {
            const titleKey = ttsVoiceDesignIdRole === "provider_slot"
                ? "app.admin.dashboard.model.hub.audio.voiceSlotRequired"
                : ttsVoiceDesignIdRole === "prefix"
                    ? "app.admin.dashboard.model.hub.audio.voicePrefixRequired"
                    : "app.admin.dashboard.model.hub.audio.voiceCustomIdRequired";
            toast({ variant: "destructive", title: t(titleKey) });
            return;
        }
        if (!isTtsDesignInputValid) {
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.voiceDesignConstraintInvalid") });
            return;
        }
        setIsTtsDesigning(true);
        setTtsDesignCandidates([]);
        try {
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: "design",
                    modelRef: selectedTtsModelRef,
                    prompt: ttsDesignPrompt.trim(),
                    previewText: ttsDesignPreviewText.trim(),
                    voiceId: ttsDesignVoiceId.trim(),
                    voiceName: ttsDesignVoiceName.trim(),
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok === false) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceDesignFailed"),
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.voiceDesignFailed")),
                });
                return;
            }
            const candidates = Array.isArray(payload.candidates)
                ? payload.candidates.filter((item: unknown): item is TtsVoiceDesignCandidate => {
                    const candidate = item as Partial<TtsVoiceDesignCandidate>;
                    return typeof candidate.generatedVoiceId === "string" && typeof candidate.previewAudio === "string";
                })
                : [];
            setTtsDesignCandidates(candidates);
            const operationPreview = String(payload.previewAudio || payload.previewAudioUrl || candidates[0]?.previewAudio || "");
            if (operationPreview) setTtsVoiceOperationPreviewUrl(operationPreview);
            if (payload.ephemeral === true || isEphemeralReferenceVoice) {
                toast({ title: t("app.admin.dashboard.model.hub.audio.referenceVoiceReady") });
                return;
            }
            const designedVoiceId = String(payload.voiceId || "").trim();
            if (designedVoiceId) {
                setModelRefTtsVoices((current) => [
                    {
                        value: designedVoiceId,
                        label: ttsDesignVoiceName.trim() || designedVoiceId,
                        group: "generated",
                        deletable: modelRefTtsVoiceCapabilities.delete === true,
                        source: modelRefTtsVoiceAssetPolicy?.inventorySource === "remote" ? "remote" : "local_projection",
                        availability: String(payload.availability || "available"),
                    },
                    ...current.filter((voice) => voice.value !== designedVoiceId),
                ]);
                setTtsModelRefValue("voice", designedVoiceId);
                if (modelRefTtsVoiceCapabilities.list === true) await handleFetchModelRefTtsVoices(false);
            }
            toast({ title: t("app.admin.dashboard.model.hub.audio.voiceDesignReady") });
        } finally {
            setIsTtsDesigning(false);
        }
    };
    const handleCommitModelRefTtsDesign = async (candidate: TtsVoiceDesignCandidate) => {
        if (!selectedTtsModelRef || !ttsDesignVoiceName.trim()) {
            toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.voiceNameRequired") });
            return;
        }
        setCommittingTtsDesignId(candidate.generatedVoiceId);
        try {
            const response = await fetch("/api/audio/model-ref-voices", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: "commit_design",
                    modelRef: selectedTtsModelRef,
                    generatedVoiceId: candidate.generatedVoiceId,
                    voiceName: ttsDesignVoiceName.trim(),
                    voiceDescription: ttsDesignPrompt.trim(),
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok === false) {
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.voiceCommitFailed"),
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.voiceCommitFailed")),
                });
                return;
            }
            const voiceId = String(payload.voiceId || "").trim();
            if (!voiceId) {
                toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.voiceCommitFailed") });
                return;
            }
            setTtsModelRefValue("voice", voiceId);
            setTtsDesignCandidates([]);
            if (modelRefTtsVoiceCapabilities.list === true) await handleFetchModelRefTtsVoices(false);
            toast({ title: t("app.admin.dashboard.model.hub.audio.voiceCommitted") });
        } finally {
            setCommittingTtsDesignId(null);
        }
    };
    const handlePreviewTts = async () => {
        if (audioConfig.tts.active_provider === "model_ref" && !String(audioConfig.tts.model_ref?.voice || "").trim()) {
            toast({
                variant: "destructive",
                title: t("app.admin.dashboard.model.hub.audio.previewFailed"),
                description: t("app.admin.dashboard.model.hub.audio.previewVoiceRequired"),
            });
            return;
        }
        setIsTtsPreviewing(true);
        try {
            const response = await fetch("/api/audio/tts/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text: ttsPreviewText.trim() || t("app.admin.dashboard.model.hub.audio.previewDefaultText"),
                    config: audioConfig,
                }),
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                toast({
                    variant: "destructive",
                    title: t("app.admin.dashboard.model.hub.audio.previewFailed"),
                    description: voiceManagerErrorText(payload, t("app.admin.dashboard.model.hub.audio.previewFailed")),
                });
                return;
            }
            const audioBlob = await response.blob();
            if (audioBlob.size === 0) {
                toast({ variant: "destructive", title: t("app.admin.dashboard.model.hub.audio.previewFailed") });
                return;
            }
            setTtsPreviewUrl(URL.createObjectURL(audioBlob));
            toast({ title: t("app.admin.dashboard.model.hub.audio.previewReady") });
            if (audioConfig.tts.active_provider === "model_ref" && modelRefTtsVoiceCapabilities.list === true) {
                await handleFetchModelRefTtsVoices(false);
            }
        } finally {
            setIsTtsPreviewing(false);
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
                await fetchData(true);
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
                <AdminSurfaceCard surface="nested" className="p-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-semibold text-foreground dark:text-slate-100">
                                <Mic className="h-4 w-4 text-muted-foreground dark:text-muted-foreground/80" />
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
                </AdminSurfaceCard>
                <AdminSurfaceCard surface="nested" className="p-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-semibold text-foreground dark:text-slate-100">
                                <Volume2 className="h-4 w-4 text-muted-foreground dark:text-muted-foreground/80" />
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
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Label>{t("app.admin.dashboard.model.hub.audio.modelRefVoiceManager")}</Label>
                                                <Badge variant="outline">
                                                    {t(`app.admin.dashboard.model.hub.audio.voicePolicy.${
                                                        isQualificationOnlyVoice
                                                            ? "qualification"
                                                            : isEphemeralReferenceVoice
                                                                ? "ephemeral"
                                                                : isProviderSlotVoice
                                                                    ? "slot"
                                                                    : "remote"
                                                    }`)}
                                                </Badge>
                                            </div>
                                            {modelRefTtsVoiceAssetPolicy?.docsUrl ? (
                                                <Button asChild type="button" variant="ghost" size="sm">
                                                    <a href={modelRefTtsVoiceAssetPolicy.docsUrl} target="_blank" rel="noreferrer">
                                                        <ExternalLink className="mr-2 h-4 w-4" />
                                                        {t("app.admin.dashboard.model.hub.audio.voiceQualificationDocs")}
                                                    </a>
                                                </Button>
                                            ) : null}
                                        </div>
                                        {isQualificationOnlyVoice ? (
                                            <div className="grid gap-3">
                                                {ttsVoicePresets.length > 0 ? (
                                                    <Select value={ttsVoicePresets.some((item) => item.value === audioConfig.tts.model_ref?.voice) ? audioConfig.tts.model_ref?.voice : undefined} onValueChange={(value) => setTtsModelRefValue("voice", value)}>
                                                        <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.model.hub.audio.selectVoice")} /></SelectTrigger>
                                                        <SelectContent>
                                                            {ttsVoicePresets.map((voice) => <SelectItem key={voice.value} value={voice.value}>{voice.label}</SelectItem>)}
                                                        </SelectContent>
                                                    </Select>
                                                ) : (
                                                    <Input value={audioConfig.tts.model_ref?.voice || ""} onChange={(event) => setTtsModelRefValue("voice", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.customVoicePlaceholder")} />
                                                )}
                                                <div className="grid gap-2 rounded-lg border border-dashed bg-background p-3">
                                                    <div className="flex flex-wrap items-center gap-2">
                                                        <Badge variant={modelRefTtsVoiceAssetPolicy?.eligibilityStatus === "eligible" ? "secondary" : "outline"}>
                                                            {t(modelRefTtsVoiceAssetPolicy?.eligibilityStatus === "eligible"
                                                                ? "app.admin.dashboard.model.hub.audio.voiceEligibilityEligible"
                                                                : "app.admin.dashboard.model.hub.audio.voiceEligibilityRequired")}
                                                        </Badge>
                                                        {modelRefTtsVoiceAssetPolicy?.consentRequired ? (
                                                            <span className="text-xs text-muted-foreground">{t("app.admin.dashboard.model.hub.audio.voiceConsentRequired")}</span>
                                                        ) : null}
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">{t("app.admin.dashboard.model.hub.audio.voiceQualificationDescription")}</p>
                                                    {modelRefTtsVoiceAssetPolicy?.applicationUrl ? (
                                                        <Button asChild type="button" variant="outline" size="sm" className="w-fit">
                                                            <a href={modelRefTtsVoiceAssetPolicy.applicationUrl} target="_blank" rel="noreferrer">
                                                                <ExternalLink className="mr-2 h-4 w-4" />
                                                                {t("app.admin.dashboard.model.hub.audio.voiceQualificationApply")}
                                                            </a>
                                                        </Button>
                                                    ) : null}
                                                </div>
                                                <Input value={audioConfig.tts.model_ref?.format || ""} onChange={(event) => setTtsModelRefValue("format", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.formatPlaceholder")} />
                                            </div>
                                        ) : (
                                            <>
                                                {isModelRefVoiceCredentialMissing ? (
                                                    <p className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-300" role="alert">
                                                        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                                                        {t("app.admin.dashboard.model.hub.audio.voiceCredentialMissing")}
                                                    </p>
                                                ) : null}
                                                {modelRefTtsVoiceCapabilities.list === true ? (
                                                    <SearchableVoiceSelect
                                                        value={audioConfig.tts.model_ref?.voice || ""}
                                                        options={modelRefTtsVoices}
                                                        placeholder={t("app.admin.dashboard.model.hub.audio.selectVoice")}
                                                        searchPlaceholder={t("app.admin.dashboard.model.hub.audio.voiceSearchPlaceholder")}
                                                        emptyLabel={t("app.admin.dashboard.model.hub.audio.voiceSearchEmpty")}
                                                        onValueChange={(value) => setTtsModelRefValue("voice", value)}
                                                        disabled={modelRefTtsVoiceListState === "loading" && modelRefTtsVoices.length === 0}
                                                        invalid={isConfiguredModelRefTtsVoiceUnavailable}
                                                        deleteLabel={t("app.admin.dashboard.model.hub.audio.deleteSelectedVoice")}
                                                        deletingValue={deletingModelRefTtsVoiceId}
                                                        onDelete={modelRefTtsVoiceCapabilities.delete === true && !isModelRefVoiceCredentialMissing
                                                            ? (voice) => void handleDeleteModelRefTtsVoice(voice)
                                                            : undefined}
                                                    />
                                                ) : isEphemeralReferenceVoice ? (
                                                    <p className="text-sm text-muted-foreground">{t("app.admin.dashboard.model.hub.audio.referenceVoiceDescription")}</p>
                                                ) : (
                                                    <Input
                                                        value={audioConfig.tts.model_ref?.voice || ""}
                                                        onChange={(event) => setTtsModelRefValue("voice", event.target.value)}
                                                        placeholder={t(isProviderSlotVoice
                                                            ? "app.admin.dashboard.model.hub.audio.voiceSlotPlaceholder"
                                                            : "app.admin.dashboard.model.hub.audio.customVoicePlaceholder")}
                                                    />
                                                )}
                                                <div className="flex flex-wrap gap-2">
                                                    {modelRefTtsVoiceCapabilities.list === true ? (
                                                        <Button type="button" variant="outline" size="sm" onClick={() => void handleFetchModelRefTtsVoices()} disabled={isModelRefTtsVoiceLoading || !selectedTtsModelRef || isModelRefVoiceCredentialMissing}>
                                                            <RefreshCw className={`mr-2 h-4 w-4 ${isModelRefTtsVoiceLoading ? "animate-spin" : ""}`} />
                                                            {t("app.admin.dashboard.model.hub.audio.fetchVoices")}
                                                        </Button>
                                                    ) : null}
                                                    {modelRefTtsVoiceCapabilities.clone === true ? (
                                                        <Button type="button" variant="outline" size="sm" onClick={() => setIsTtsClonePanelOpen((current) => !current)} disabled={isModelRefVoiceCredentialMissing}>
                                                            <Upload className="mr-2 h-4 w-4" />
                                                            {t(isEphemeralReferenceVoice
                                                                ? "app.admin.dashboard.model.hub.audio.referenceVoiceUpload"
                                                                : "app.admin.dashboard.model.hub.audio.uploadVoice")}
                                                        </Button>
                                                    ) : null}
                                                    {modelRefTtsVoiceCapabilities.design === true ? (
                                                        <Button type="button" variant="outline" size="sm" onClick={() => setIsTtsDesignPanelOpen((current) => !current)} disabled={isModelRefVoiceCredentialMissing}>
                                                            <Plus className="mr-2 h-4 w-4" />
                                                            {t("app.admin.dashboard.model.hub.audio.voiceDesignAction")}
                                                        </Button>
                                                    ) : null}
                                                </div>
                                                {selectedModelRefTtsVoice?.availability === "pending_activation" ? (
                                                    <p className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-300">
                                                        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                                                        {t("app.admin.dashboard.model.hub.audio.voicePendingActivation")}
                                                    </p>
                                                ) : null}
                                                {isConfiguredModelRefTtsVoiceUnavailable ? (
                                                    <p className="flex items-start gap-2 text-xs text-destructive" role="alert">
                                                        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                                                        {t("app.admin.dashboard.model.hub.audio.voiceUnavailable")}
                                                    </p>
                                                ) : null}
                                                {modelRefTtsVoiceCapabilities.clone === true && isTtsClonePanelOpen ? (
                                                    <div className="grid gap-2 rounded-lg border border-dashed bg-background p-3">
                                                        <Label>{t(isEphemeralReferenceVoice
                                                            ? "app.admin.dashboard.model.hub.audio.referenceVoiceTitle"
                                                            : "app.admin.dashboard.model.hub.audio.voiceCloneTitle")}</Label>
                                                        {!isEphemeralReferenceVoice ? (
                                                            <Input value={ttsCloneVoiceId} onChange={(event) => setTtsCloneVoiceId(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.voiceCloneIdPlaceholder")} />
                                                        ) : null}
                                                        {modelRefTtsVoiceCapabilities.preview === true ? (
                                                            <Input value={ttsClonePreviewText} onChange={(event) => setTtsClonePreviewText(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.voiceClonePreviewPlaceholder")} />
                                                        ) : null}
                                                        <Input type="file" accept=".mp3,.m4a,.wav,audio/mpeg,audio/mp4,audio/wav" onChange={(event) => setTtsCloneFile(event.target.files?.[0] || null)} />
                                                        <p className="text-xs text-muted-foreground">
                                                            {t("app.admin.dashboard.model.hub.audio.voiceCloneSampleHint", {
                                                                seconds: modelRefTtsVoiceInfo?.sampleLimits?.minDurationSeconds ?? 10,
                                                            })}
                                                        </p>
                                                        <Button type="button" variant="outline" size="sm" onClick={() => void handleCloneModelRefTtsVoice()} disabled={isTtsCloning || !ttsCloneFile || (!isEphemeralReferenceVoice && !ttsCloneVoiceId.trim()) || (isEphemeralReferenceVoice && !ttsClonePreviewText.trim())}>
                                                            {isTtsCloning ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
                                                            {isTtsCloning
                                                                ? t("app.admin.dashboard.model.hub.audio.voiceCloning")
                                                                : t(isEphemeralReferenceVoice
                                                                    ? "app.admin.dashboard.model.hub.audio.referenceVoiceCreate"
                                                                    : "app.admin.dashboard.model.hub.audio.voiceCloneUpload")}
                                                        </Button>
                                                    </div>
                                                ) : null}
                                                {modelRefTtsVoiceCapabilities.design === true && isTtsDesignPanelOpen ? (
                                                    <div className="grid gap-2 rounded-lg border border-dashed bg-background p-3">
                                                        <Label>{t("app.admin.dashboard.model.hub.audio.voiceDesignTitle")}</Label>
                                                        <div className="grid gap-1">
                                                            <Textarea
                                                                value={ttsDesignPrompt}
                                                                onChange={(event) => setTtsDesignPrompt(event.target.value)}
                                                                minLength={ttsDesignPromptMinChars}
                                                                maxLength={ttsDesignPromptMaxChars}
                                                                placeholder={t("app.admin.dashboard.model.hub.audio.voiceDesignPromptPlaceholder")}
                                                            />
                                                            {ttsDesignPromptMaxChars !== undefined || ttsDesignPromptMinChars > 1 ? (
                                                                <p className="text-right text-xs text-muted-foreground">
                                                                    {t("app.admin.dashboard.model.hub.audio.voiceDesignLength", {
                                                                        count: ttsDesignPrompt.length,
                                                                        min: ttsDesignPromptMinChars,
                                                                        max: ttsDesignPromptMaxChars ?? "\u221e",
                                                                    })}
                                                                </p>
                                                            ) : null}
                                                        </div>
                                                        <div className="grid gap-1">
                                                            <Input
                                                                value={ttsDesignPreviewText}
                                                                onChange={(event) => setTtsDesignPreviewText(event.target.value)}
                                                                minLength={ttsDesignPreviewMinChars}
                                                                maxLength={ttsDesignPreviewMaxChars}
                                                                placeholder={t("app.admin.dashboard.model.hub.audio.voiceDesignPreviewPlaceholder")}
                                                            />
                                                            {ttsDesignPreviewMaxChars !== undefined || ttsDesignPreviewMinChars > 1 ? (
                                                                <p className="text-right text-xs text-muted-foreground">
                                                                    {t("app.admin.dashboard.model.hub.audio.voiceDesignLength", {
                                                                        count: ttsDesignPreviewText.length,
                                                                        min: ttsDesignPreviewMinChars,
                                                                        max: ttsDesignPreviewMaxChars ?? "\u221e",
                                                                    })}
                                                                </p>
                                                            ) : null}
                                                        </div>
                                                        {isTtsVoiceDesignIdVisible ? (
                                                            <div className="grid gap-1">
                                                                <Input
                                                                    value={ttsDesignVoiceId}
                                                                    onChange={(event) => setTtsDesignVoiceId(event.target.value)}
                                                                    minLength={ttsDesignVoiceIdMinChars}
                                                                    maxLength={ttsDesignVoiceIdMaxChars}
                                                                    aria-invalid={Boolean(ttsDesignVoiceId.trim()) && !isTtsDesignVoiceIdFormatValid}
                                                                    placeholder={t(ttsDesignVoiceIdPlaceholderKey)}
                                                                />
                                                                {ttsDesignVoiceIdMaxChars !== undefined || (ttsDesignVoiceIdMinChars ?? 0) > 1 ? (
                                                                    <p className="text-right text-xs text-muted-foreground">
                                                                        {t("app.admin.dashboard.model.hub.audio.voiceDesignLength", {
                                                                            count: ttsDesignVoiceId.length,
                                                                            min: ttsDesignVoiceIdMinChars ?? 0,
                                                                            max: ttsDesignVoiceIdMaxChars ?? "\u221e",
                                                                        })}
                                                                    </p>
                                                                ) : null}
                                                            </div>
                                                        ) : null}
                                                        {modelRefTtsVoiceAssetPolicy?.designFlow === "preview_then_commit" ? (
                                                            <Input value={ttsDesignVoiceName} onChange={(event) => setTtsDesignVoiceName(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.voiceNamePlaceholder")} />
                                                        ) : null}
                                                        <Button type="button" variant="outline" size="sm" onClick={() => void handleDesignModelRefTtsVoice()} disabled={isTtsDesigning || !isTtsDesignInputValid}>
                                                            {isTtsDesigning ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Volume2 className="mr-2 h-4 w-4" />}
                                                            {isTtsDesigning
                                                                ? t("app.admin.dashboard.model.hub.audio.voiceDesigning")
                                                                : t("app.admin.dashboard.model.hub.audio.voiceDesignGenerate")}
                                                        </Button>
                                                        {ttsDesignCandidates.map((candidate, index) => (
                                                            <div key={candidate.generatedVoiceId} className="grid gap-2 rounded-md border bg-muted/30 p-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                                                                <audio controls preload="metadata" src={candidate.previewAudio} className="h-9 w-full">
                                                                    {t("app.admin.dashboard.model.hub.audio.previewUnsupported")}
                                                                </audio>
                                                                <Button type="button" size="sm" onClick={() => void handleCommitModelRefTtsDesign(candidate)} disabled={committingTtsDesignId !== null || !ttsDesignVoiceName.trim()}>
                                                                    {committingTtsDesignId === candidate.generatedVoiceId
                                                                        ? t("app.admin.dashboard.model.hub.audio.voiceCommitting")
                                                                        : t("app.admin.dashboard.model.hub.audio.voiceCommitAction", { index: index + 1 })}
                                                                </Button>
                                                            </div>
                                                        ))}
                                                    </div>
                                                ) : null}
                                                {ttsVoiceOperationPreviewUrl ? (
                                                    <audio controls autoPlay preload="metadata" src={ttsVoiceOperationPreviewUrl} className="h-10 w-full">
                                                        {t("app.admin.dashboard.model.hub.audio.previewUnsupported")}
                                                    </audio>
                                                ) : null}
                                                {!isEphemeralReferenceVoice ? (
                                                    <Input value={audioConfig.tts.model_ref?.format || ""} onChange={(event) => setTtsModelRefValue("format", event.target.value)} placeholder={t("app.admin.dashboard.model.hub.audio.formatPlaceholder")} />
                                                ) : null}
                                            </>
                                        )}
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
                        <div className="grid gap-2 rounded-xl border border-dashed p-3">
                            <Label>{t("app.admin.dashboard.model.hub.audio.previewTitle")}</Label>
                            <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
                                <Input
                                    value={ttsPreviewText}
                                    onChange={(event) => setTtsPreviewText(event.target.value)}
                                    maxLength={300}
                                    placeholder={t("app.admin.dashboard.model.hub.audio.previewTextPlaceholder")}
                                />
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={() => void handlePreviewTts()}
                                    disabled={isTtsPreviewing}
                                >
                                    {isTtsPreviewing ? (
                                        <LoaderCircle className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                                    ) : (
                                        <Volume2 className="mr-2 h-4 w-4" aria-hidden="true" />
                                    )}
                                    {isTtsPreviewing
                                        ? t("app.admin.dashboard.model.hub.audio.previewing")
                                        : t("app.admin.dashboard.model.hub.audio.previewAction")}
                                </Button>
                            </div>
                            {ttsPreviewUrl ? (
                                <audio
                                    key={ttsPreviewUrl}
                                    controls
                                    autoPlay
                                    preload="metadata"
                                    src={ttsPreviewUrl}
                                    className="h-10 w-full"
                                >
                                    {t("app.admin.dashboard.model.hub.audio.previewUnsupported")}
                                </audio>
                            ) : null}
                        </div>
                    </div>
                </AdminSurfaceCard>
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
                        <Button variant="outline" onClick={() => void fetchData(true)} disabled={isLoading}>
                            <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`}/>
                            {t("app.admin.dashboard.model.hub.page.k876e8c06")}
                        </Button>
                        <Button onClick={() => {
                setEditingProvider(null);
                setProviderType("API");
                setProviderCredentialMode("apiKey");
                setProviderApiStandard("openai");
                setProviderBaseUrl("");
                setProviderChannels([createProviderChannel("openai", "", "default")]);
                setProviderDefaultChannelId("default");
                setProviderApiKey("");
                setProviderOauthPath("");
                setPlatformLoginPreset("codex");
                setLocalBackendPreset("ollama");
                setIsProviderDialogOpen(true);
            }}>
                            <Plus className="mr-2 h-4 w-4"/>
                            {t("app.admin.dashboard.model.hub.page.k9e31d9ed")}
                        </Button>
                        <Button disabled={providers.length === 0} onClick={() => {
                            const provider = providers[0];
                            const channel = provider?.channels?.find((item) => item.id === provider.defaultChannelId) || provider?.channels?.[0];
                            setEditingModel(null);
                            setModelType("TEXT");
                            setMediaCapabilityModes([]);
                            setModelProviderId(provider?.id || "");
                            setModelChannelId(channel?.id || "");
                            setModelWireProtocol((channel?.defaultWireProtocol || "") as ModelWireProtocol);
                            setRerankApiFlavor("generic");
                            setComfyWorkflow(EMPTY_COMFY_WORKFLOW);
                            setIsModelDialogOpen(true);
                        }}>
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
                <AdminSurfaceCard surface="nested" className="p-4">
                        <div className="text-sm font-semibold">{t("app.admin.dashboard.model.hub.catalog.apiProvider")}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.model.hub.catalog.apiProviderPurposeHint")}</div>
                        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                            {CATALOG_PURPOSES.map((purpose) => (
                                <button
                                    key={purpose.id}
                                    type="button"
                                    className={`rounded-xl border px-3 py-2 text-left transition ${catalogPurpose === purpose.id ? "border-primary/40 bg-primary/10 text-foreground" : "border-border bg-background hover:bg-muted"}`}
                                    onClick={() => {
                                        setCatalogPurpose(purpose.id);
                                        pendingCatalogProviderIdRef.current = "";
                                        setCatalogApiKey("");
                                        setCatalogVoiceAppId("");
                                        setCatalogVoiceResourceId("");
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
                                    <span className="mt-1 block truncate text-[11px] text-muted-foreground">{t(purpose.hintKey)}</span>
                                </button>
                            ))}
                        </div>
                        <div className="mt-3 grid gap-3 md:grid-cols-[1fr_1.2fr_auto]">
                            <HydrationSafeClientOnly fallback={<div className="h-10 rounded-md border bg-background px-3 py-2 text-sm text-muted-foreground">{selectedCatalogProvider?.name || t("app.admin.dashboard.model.hub.catalog.selectProvider")}</div>}>
                                <Select value={selectedCatalogProviderId} disabled={isLoading} onValueChange={(value) => {
                                    pendingCatalogProviderIdRef.current = "";
                                    setSelectedCatalogProviderId(value);
                                    setCatalogApiKey("");
                                    setCatalogVoiceAppId("");
                                    setCatalogVoiceResourceId("");
                                    setCatalogProbeModels([]);
                                    setSelectedCatalogModelId("");
                                    setCatalogModelFilter("");
                                    setProbedCatalogProviderId("");
                                    setCatalogProbeStatus(null);
                                    setManualModelEntryEnabled(false);
                                    setCatalogRuntimeProtocol("default");
                                }}>
                                    <SelectTrigger data-testid="quick-connect-provider-trigger">
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
                        {catalogPurpose === "chat" && selectedCatalogChannels.length > 1 ? (
                            <div className="mt-3 grid gap-2 rounded-xl border border-dashed px-3 py-2">
                                <Label className="text-xs font-semibold">{t("app.admin.dashboard.model.hub.channel.catalogEntryTitle")}</Label>
                                <HydrationSafeClientOnly fallback={<div className="h-9 rounded-md border bg-background px-3 py-2 text-sm text-foreground dark:text-slate-300">{selectedCatalogRuntime.label}</div>}>
                                    <Select value={selectedCatalogRuntime.channelId} onValueChange={(value: CatalogRuntimeProtocol) => setCatalogRuntimeProtocol(value)}>
                                        <SelectTrigger className="h-9" data-testid="quick-connect-entry-trigger">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {selectedCatalogChannels.map((channel) => <SelectItem key={channel.id} value={channel.id}>{channel.label} · {channel.apiStandard}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </HydrationSafeClientOnly>
                                <div className="text-xs text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.channel.runtimeHint", { baseUrl: selectedCatalogRuntime.baseUrl, protocol: selectedCatalogRuntime.apiStandard })}
                                </div>
                            </div>
                        ) : null}
                        {selectedCatalogProviderId === "__custom__" ? (
                            <div className="mt-3 grid gap-3 md:grid-cols-2">
                                <Input className="md:col-span-2" value={customProviderName} onChange={(event) => setCustomProviderName(event.target.value)} placeholder={t("app.admin.dashboard.model.hub.catalog.customNamePlaceholder")}/>
                                <div className="md:col-span-2 space-y-2 rounded-xl border border-border/70 p-3">
                                    <Label className="text-xs font-semibold">{t("app.admin.dashboard.model.hub.channel.title")}</Label>
                                    {PROVIDER_CHANNEL_PRESETS.filter((preset) => catalogPurpose === "chat" ? preset.apiStandard !== "comfyui" : preset.apiStandard === "openai" || (catalogPurpose === "workflow" && preset.apiStandard === "comfyui")).map((preset) => {
                                        const channel = customProviderChannels.find((item) => item.id === preset.id);
                                        const checked = Boolean(channel);
                                        return (
                                            <div key={preset.id} className="grid gap-2 rounded-lg border border-border/60 p-2 md:grid-cols-[auto_auto_minmax(0,1fr)] md:items-center">
                                                <Checkbox
                                                    checked={checked}
                                                    disabled={checked && customProviderChannels.length === 1}
                                                    onCheckedChange={(next) => {
                                                        if (next === true) {
                                                            const nextChannel = createProviderChannel(preset.apiStandard, "", preset.id);
                                                            setCustomProviderChannels((current) => [...current, nextChannel]);
                                                            if (!customProviderDefaultChannelId) setCustomProviderDefaultChannelId(nextChannel.id);
                                                        } else {
                                                            const remaining = customProviderChannels.filter((item) => item.id !== preset.id);
                                                            setCustomProviderChannels(remaining);
                                                            if (customProviderDefaultChannelId === preset.id) setCustomProviderDefaultChannelId(remaining[0]?.id || "");
                                                        }
                                                    }}
                                                />
                                                <label className="flex items-center gap-1.5 text-sm">
                                                    <input
                                                        type="radio"
                                                        name="custom-default-channel"
                                                        checked={customProviderDefaultChannelId === preset.id}
                                                        disabled={!checked}
                                                        onChange={() => setCustomProviderDefaultChannelId(preset.id)}
                                                    />
                                                    {t(preset.labelKey)}
                                                </label>
                                                <Input
                                                    value={channel?.baseUrl || ""}
                                                    disabled={!checked}
                                                    onChange={(event) => {
                                                        const nextBaseUrl = event.target.value;
                                                        setCustomProviderChannels((current) => current.map((item) => item.id === preset.id ? { ...item, baseUrl: nextBaseUrl } : item));
                                                        if (customProviderDefaultChannelId === preset.id) setCustomProviderBaseUrl(nextBaseUrl);
                                                    }}
                                                    placeholder={t("app.admin.dashboard.model.hub.catalog.customBaseUrlPlaceholder")}
                                                />
                                            </div>
                                        );
                                    })}
                                    <p className="text-xs text-muted-foreground">{t("app.admin.dashboard.model.hub.channel.customHelp")}</p>
                                </div>
                                <div className="md:col-span-2">
                                    <Label className="text-xs font-semibold">{t("app.admin.dashboard.model.hub.catalog.capabilities")}</Label>
                                    <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                                        {CUSTOM_PROVIDER_CAPABILITIES.map((capability) => {
                                            const checked = customProviderCapabilities.includes(capability.id);
                                            return (
                                                <label key={capability.id} className="flex min-h-10 cursor-pointer items-center gap-2 rounded-xl border border-border bg-background px-3 py-2 text-sm">
                                                    <Checkbox
                                                        checked={checked}
                                                        onCheckedChange={(next) => setCustomProviderCapabilities((current) => (
                                                            next
                                                                ? Array.from(new Set([...current, capability.id]))
                                                                : current.filter((item) => item !== capability.id)
                                                        ))}
                                                    />
                                                    <span>{t(capability.labelKey)}</span>
                                                </label>
                                            );
                                        })}
                                    </div>
                                </div>
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
                            <div className={`mt-3 rounded-xl border px-3 py-2 text-xs ${catalogProbeStatus.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200" : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"}`}>
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
                                            const providerModelId = typeof model.mediaLimits?.providerModelId === "string" ? model.mediaLimits.providerModelId : "";
                                            const hasExplicitRoute = Boolean(providerModelId && providerModelId !== modelId);
                                            const visibleModelPath = hasExplicitRoute ? modelId.replace(/^\/+/, "") : modelId;
                                            const modelIcon = resolveModelIcon({
                                                modelId: providerModelId || modelId,
                                                providerId: probedCatalogProviderId || selectedCatalogProvider?.id || selectedCatalogProviderId,
                                                providerName: selectedCatalogProvider?.name || "",
                                                explicitAsset: model.logoAsset || null,
                                            });
                                            return (
                                                <button
                                                    key={`${probedCatalogProviderId || selectedCatalogProviderId}:${modelId}`}
                                                    type="button"
                                                    className={`mb-1 flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${selectedCatalogModelId === modelId ? "bg-primary/10 text-foreground" : "hover:bg-muted"}`}
                                                    onClick={() => {
                                                        setSelectedCatalogModelId(modelId);
                                                        setCatalogModelFilter(modelId);
                                                    }}
                                                >
                                                    <span className="flex min-w-0 items-center gap-2">
                                                        <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md ${selectedCatalogModelId === modelId ? "bg-primary/15" : "bg-muted"}`}>
                                                            {modelIcon ? <Image src={modelIcon} alt="" width={16} height={16} className="h-4 w-4 object-contain" unoptimized /> : null}
                                                        </span>
                                                        <span className="min-w-0">
                                                            <span className="block truncate">{providerModelId || modelId}</span>
                                                            {hasExplicitRoute ? (
                                                                <span className="block truncate text-[10px] opacity-70">
                                                                    {t("app.admin.dashboard.model.hub.catalog.modelRoute", { route: visibleModelPath })}
                                                                </span>
                                                            ) : null}
                                                        </span>
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
                </AdminSurfaceCard>
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
                    const nextChannels = editableProviderChannels(provider, provider.apiStandard || "openai", provider.baseUrl || "");
                    setProviderChannels(nextChannels);
                    setProviderDefaultChannelId(provider.defaultChannelId || nextChannels[0]?.id || "default");
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
                    await fetchData(true);
                }}/>))}
                    </div>)}
            </ConfigCard>

            <ConfigCard title={t("app.admin.dashboard.model.hub.page.k6a95644c")} description={t("app.admin.dashboard.model.hub.page.k933aeed1")} variant="list" allowOverflow>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="text-sm text-muted-foreground dark:text-muted-foreground/80">{t("app.admin.dashboard.model.hub.page.kdea3cadf")}</div>
                    <HydrationSafeClientOnly
                        fallback={
                            <div className="grid w-full max-w-5xl grid-cols-4 rounded-2xl bg-muted p-1 text-center text-sm dark:bg-card/10 md:grid-cols-6 xl:grid-cols-11">
                                {[t("app.admin.dashboard.model.hub.page.ke8cc995b"), t("app.admin.dashboard.model.hub.page.kc4eaa582"), t("app.admin.dashboard.model.hub.page.k2d2f7b56"), t("app.admin.dashboard.model.hub.catalog.tabImage"), t("app.admin.dashboard.model.hub.catalog.tabVideo"), t("app.admin.dashboard.model.hub.catalog.tabVoice"), t("app.admin.dashboard.model.hub.catalog.tabMusic"), t("app.admin.dashboard.model.hub.catalog.tabWorkflow"), t("app.admin.dashboard.model.hub.catalog.tabModel3d"), t("app.admin.dashboard.model.hub.page.kc1798b61"), t("app.admin.dashboard.model.hub.page.k81ac6b74")].map((label, index) => (
                                    <span key={`${label}-${index}`} className="rounded-md px-3 py-1 text-muted-foreground dark:text-slate-300">{label}</span>
                                ))}
                            </div>
                        }
                    >
                        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full max-w-5xl">
                            <TabsList className="grid w-full grid-cols-4 rounded-2xl bg-muted dark:bg-card/10 md:grid-cols-6 xl:grid-cols-11">
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
                    const storedMediaLimits = model.mediaLimits || {};
                    const controlMediaLimits = controlMeta?.mediaLimits || {};
                    const capabilityModes = Object.prototype.hasOwnProperty.call(storedMediaLimits, "capabilityModes")
                        ? storedMediaLimits.capabilityModes
                        : controlMediaLimits.capabilityModes;
                    const operationCapabilityProfiles = Object.prototype.hasOwnProperty.call(storedMediaLimits, "operationCapabilityProfiles")
                        ? storedMediaLimits.operationCapabilityProfiles
                        : controlMediaLimits.operationCapabilityProfiles;
                    setMediaCapabilityModes(resolveMediaCapabilityModes(
                        model.type || "TEXT",
                        capabilityModes,
                        Array.isArray(storedMediaLimits.operationKinds)
                            ? storedMediaLimits.operationKinds
                            : Array.isArray(controlMediaLimits.operationKinds)
                                ? controlMediaLimits.operationKinds
                                : model.operationKinds || [model.endpointBinding?.operationKind],
                        operationCapabilityProfiles,
                    ));
                    setModelProviderId(model.providerId);
                    setModelChannelId(String(model.endpointBinding?.channelId || ""));
                    setModelWireProtocol(String(model.endpointBinding?.wireProtocol || "") as ModelWireProtocol);
                    setRerankApiFlavor(model.rerankApiFlavor || "generic");
                    setComfyWorkflow(comfyWorkflowDraft(model.mediaLimits));
                    setIsModelDialogOpen(true);
                }} onDelete={handleDeleteModel} onTestConnection={handleTestConnection} onRepairReasoning={handleRepairReasoning} onSetReasoningLevel={(level) => handleSetReasoningLevel(model, controlMeta, level)} onToggleProviderHostedTools={(enabled) => handleToggleProviderHostedTools(model, controlMeta, enabled)} onSetDefault={handleSetDefaultModel}/>);
                        })}
                    </div>)}
            </ConfigCard>

            {hasLoadedAudioConfig ? systemAudioConfigCard : (
                <ConfigCard title={t("app.admin.dashboard.model.hub.audio.systemTitle")} description={t("app.admin.dashboard.model.hub.audio.systemDescription")} variant="list">
                    <div className="flex min-h-44 items-center justify-center gap-2 text-sm text-muted-foreground" role="status" aria-live="polite">
                        <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />
                        {t("app.admin.dashboard.model.hub.audio.loadingConfig")}
                    </div>
                </ConfigCard>
            )}

            {hubEnvelope ? (<SourceMetaRow source={hubEnvelope.source} savePath={hubEnvelope.savePath} reloadRequired={hubEnvelope.reloadRequired}/>) : null}

            <Dialog open={isProviderDialogOpen} onOpenChange={setIsProviderDialogOpen}>
                <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
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
                            </>) : (<div className="space-y-3 rounded-xl border border-border/70 p-3">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <Label>{t("app.admin.dashboard.model.hub.channel.title")}</Label>
                                        <p className="mt-1 text-xs text-muted-foreground">{t("app.admin.dashboard.model.hub.channel.help")}</p>
                                    </div>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => {
                                            const used = new Set(providerChannels.map((channel) => channel.id));
                                            const preset = PROVIDER_CHANNEL_PRESETS.find((item) => !used.has(item.id)) || PROVIDER_CHANNEL_PRESETS[0];
                                            const suffix = providerChannels.filter((channel) => channel.id.startsWith(preset.id)).length + 1;
                                            setProviderChannels((current) => [...current, createProviderChannel(preset.apiStandard, "", used.has(preset.id) ? `${preset.id}-${suffix}` : preset.id)]);
                                        }}
                                    >
                                        <Plus className="mr-1 h-3.5 w-3.5" />{t("app.admin.dashboard.model.hub.channel.add")}
                                    </Button>
                                </div>
                                {providerChannels.map((channel, index) => (
                                    <div key={`${channel.id}-${index}`} className="grid gap-2 rounded-lg border border-border/60 bg-muted/10 p-2 md:grid-cols-2">
                                        <div className="flex items-center gap-2 md:col-span-2">
                                            <input
                                                type="radio"
                                                name="default-provider-channel"
                                                checked={providerDefaultChannelId === channel.id}
                                                onChange={() => setProviderDefaultChannelId(channel.id)}
                                                aria-label={t("app.admin.dashboard.model.hub.channel.default")}
                                            />
                                            <span className="text-xs text-muted-foreground">{t("app.admin.dashboard.model.hub.channel.default")}</span>
                                            {providerChannels.length > 1 ? (
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="sm"
                                                    className="ml-auto h-7"
                                                    onClick={() => {
                                                        const next = providerChannels.filter((_, itemIndex) => itemIndex !== index);
                                                        setProviderChannels(next);
                                                        if (providerDefaultChannelId === channel.id) setProviderDefaultChannelId(next[0]?.id || "");
                                                    }}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5" />
                                                </Button>
                                            ) : null}
                                        </div>
                                        <Input
                                            value={channel.id}
                                            onChange={(event) => {
                                                const previousId = channel.id;
                                                const nextId = event.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, "");
                                                setProviderChannels((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, id: nextId } : item));
                                                if (providerDefaultChannelId === previousId) setProviderDefaultChannelId(nextId);
                                            }}
                                            placeholder="openai"
                                            aria-label={t("app.admin.dashboard.model.hub.channel.id")}
                                        />
                                        <Input
                                            value={channel.label}
                                            onChange={(event) => setProviderChannels((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, label: event.target.value } : item))}
                                            placeholder={t("app.admin.dashboard.model.hub.channel.label")}
                                        />
                                        <select
                                            value={channel.apiStandard}
                                            onChange={(event) => {
                                                const preset = PROVIDER_CHANNEL_PRESETS.find((item) => item.apiStandard === event.target.value) || PROVIDER_CHANNEL_PRESETS[0];
                                                setProviderChannels((current) => current.map((item, itemIndex) => itemIndex === index ? {
                                                    ...item,
                                                    apiStandard: preset.apiStandard,
                                                    wireProtocols: [...preset.wireProtocols],
                                                    defaultWireProtocol: preset.defaultWireProtocol,
                                                } : item));
                                            }}
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground"
                                        >
                                            {PROVIDER_CHANNEL_PRESETS.map((preset) => <option key={preset.id} value={preset.apiStandard}>{t(preset.labelKey)}</option>)}
                                        </select>
                                        <select
                                            value={channel.defaultWireProtocol}
                                            disabled={channel.wireProtocols.length === 0}
                                            onChange={(event) => setProviderChannels((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, defaultWireProtocol: event.target.value } : item))}
                                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground disabled:opacity-60"
                                        >
                                            {channel.wireProtocols.length === 0 ? <option value="">{t("app.admin.dashboard.model.hub.channel.noWireProtocol")}</option> : null}
                                            {MODEL_WIRE_PROTOCOLS.filter((protocol) => channel.wireProtocols.includes(protocol.id)).map((protocol) => <option key={protocol.id} value={protocol.id}>{t(protocol.labelKey)}</option>)}
                                        </select>
                                        <Input
                                            className="md:col-span-2"
                                            value={channel.baseUrl}
                                            onChange={(event) => setProviderChannels((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, baseUrl: event.target.value } : item))}
                                            placeholder={t("app.admin.dashboard.model.hub.catalog.customBaseUrlPlaceholder")}
                                        />
                                        {channel.apiStandard === "anthropic" ? (
                                            <p className={`md:col-span-2 text-xs ${/\/v1\/?$/i.test(channel.baseUrl.trim()) ? "text-destructive" : "text-muted-foreground"}`}>
                                                {t("app.admin.dashboard.model.hub.channel.anthropicBaseUrlHelp")}
                                            </p>
                                        ) : null}
                                        {channel.apiStandard === "gemini" ? (
                                            <Input
                                                className="md:col-span-2"
                                                value={channel.apiVersion}
                                                onChange={(event) => setProviderChannels((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, apiVersion: event.target.value } : item))}
                                                placeholder={t("app.admin.dashboard.model.hub.channel.apiVersionPlaceholder")}
                                            />
                                        ) : null}
                                    </div>
                                ))}
                            </div>)}
                        {providerType !== "API" ? (<div className="space-y-2">
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
                        </div>) : null}
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
                            <Select value={modelProviderId} onValueChange={(value) => {
                                const provider = providers.find((item) => item.id === value);
                                const channel = provider?.channels?.find((item) => item.id === provider.defaultChannelId) || provider?.channels?.[0];
                                setModelProviderId(value);
                                setModelChannelId(channel?.id || "");
                                setModelWireProtocol((channel?.defaultWireProtocol || "") as ModelWireProtocol);
                            }}>
                                <SelectTrigger id="model-provider"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    {providers.map((provider) => (<SelectItem key={provider.id} value={provider.id}>{provider.name}</SelectItem>))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-channel">{t("app.admin.dashboard.model.hub.channel.modelChannel")}</Label>
                            <select
                                id="model-channel"
                                name="channelId"
                                value={modelChannelId || selectedModelChannel?.id || ""}
                                onChange={(event) => {
                                    const channel = selectedModelProvider?.channels?.find((item) => item.id === event.target.value);
                                    setModelChannelId(event.target.value);
                                    setModelWireProtocol((channel?.defaultWireProtocol || "") as ModelWireProtocol);
                                }}
                                disabled={!selectedModelProvider?.channels?.length}
                                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                            >
                                {(selectedModelProvider?.channels || []).map((channel) => (
                                    <option key={channel.id} value={channel.id}>
                                        {channel.label} · {channel.apiStandard} · {channel.baseUrl}
                                    </option>
                                ))}
                            </select>
                            <p className="text-xs leading-5 text-muted-foreground">
                                {t("app.admin.dashboard.model.hub.channel.modelChannelHelp")}
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-model-id">{t("app.admin.dashboard.model.hub.page.k8dbca6d6")}</Label>
                            <Input id="model-model-id" name="modelId" defaultValue={editingModel?.modelId || ""} required/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="model-type">{t("app.admin.dashboard.model.hub.page.k0bce4283")}</Label>
                            <input type="hidden" name="type" value={modelType}/>
                            <Select value={modelType} onValueChange={(value) => {
                                setModelType(value);
                                setMediaCapabilityModes(resolveMediaCapabilityModes(value, undefined, []));
                            }}>
                                <SelectTrigger id="model-type"><SelectValue>{resolveAdminLabel(t, "modelType", modelType)}</SelectValue></SelectTrigger>
                                <SelectContent>
                                    {["TEXT", "MULTIMODAL", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D", "MEDIA", "EMBEDDING", "RERANK"].map((value) => <SelectItem key={value} value={value}>{resolveAdminLabel(t, "modelType", value)}</SelectItem>)}
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
                        {modelType === "TEXT" || modelType === "MULTIMODAL" || modelType === "VISION" || modelType === "CHAT" ? (
                            <div className="space-y-4">
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
                                <div className="space-y-2">
                                    <Label htmlFor="model-wire-protocol">{t("app.admin.dashboard.model.hub.page.wireProtocol")}</Label>
                                    <select
                                        id="model-wire-protocol"
                                        name="wireProtocol"
                                        value={modelWireProtocol}
                                        onChange={(event) => setModelWireProtocol(event.target.value as ModelWireProtocol)}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    >
                                        <option value="">{t("app.admin.dashboard.model.hub.page.wireProtocolAuto")}</option>
                                        {MODEL_WIRE_PROTOCOLS.filter((protocol) => !selectedModelChannel?.wireProtocols?.length || selectedModelChannel.wireProtocols.includes(protocol.id)).map((protocol) => (
                                            <option key={protocol.id} value={protocol.id}>{t(protocol.labelKey)}</option>
                                        ))}
                                    </select>
                                    <p className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.model.hub.page.wireProtocolHelp")}</p>
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
                            <div className="space-y-4">
                                <AdminHoverInfo
                                    content={t("app.admin.dashboard.model.hub.catalog.mediaModelNotice")}
                                    panelClassName="text-xs leading-5"
                                >
                                    <Badge variant="secondary">
                                        {t("app.admin.dashboard.model.hub.catalog.mediaModelNoticeTitle")}
                                    </Badge>
                                </AdminHoverInfo>
                                {modelType !== "WORKFLOW" ? <div className="grid gap-4 md:grid-cols-2">
                                    <div className="space-y-2">
                                        <Label htmlFor="model-endpoint-path">{t("app.admin.dashboard.model.hub.page.manualEndpointPath")}</Label>
                                        <Input
                                            id="model-endpoint-path"
                                            name="endpointPath"
                                            defaultValue={String(editingModel?.endpointBinding?.endpointPath || editingModel?.mediaLimits?.endpointPath || "")}
                                            placeholder="images/generations"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="model-provider-model-id">{t("app.admin.dashboard.model.hub.page.manualProviderModelId")}</Label>
                                        <Input
                                            id="model-provider-model-id"
                                            name="providerModelId"
                                            defaultValue={String(editingModel?.endpointBinding?.providerModelId || editingModel?.mediaLimits?.providerModelId || "")}
                                            placeholder="gpt-image-2"
                                        />
                                    </div>
                                </div> : null}
                                {modelType === "WORKFLOW" ? (
                                    <div className="space-y-2">
                                        <Label>{t("app.admin.dashboard.model.hub.page.mediaAdapter")}</Label>
                                        <input type="hidden" name="adapter" value="comfyui_workflow" />
                                        <div className="flex h-10 items-center rounded-md border border-input bg-muted/30 px-3 text-sm">ComfyUI Workflow</div>
                                    </div>
                                ) : <div className="space-y-2">
                                    <Label htmlFor="model-media-adapter">{t("app.admin.dashboard.model.hub.page.mediaAdapter")}</Label>
                                    <select
                                        id="model-media-adapter"
                                        name="adapter"
                                        defaultValue={String(editingModel?.endpointBinding?.adapter || editingModel?.mediaLimits?.adapter || "")}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    >
                                        <option value="">{t("app.admin.dashboard.model.hub.page.mediaAdapterUnbound")}</option>
                                        <option value="openai_images">OpenAI Images</option>
                                        <option value="agnes_images">Agnes Images</option>
                                        <option value="agnes_video">Agnes Video</option>
                                        <option value="volcengine_ark">Volcengine Ark</option>
                                        <option value="dashscope">Alibaba Cloud Model Studio</option>
                                        <option value="comfyui_workflow">ComfyUI Workflow</option>
                                        <option value="minimax_video">MiniMax Video</option>
                                        <option value="minimax_tts">MiniMax Speech</option>
                                        <option value="minimax_music">MiniMax Music</option>
                                        <option value="mureka_music">Mureka Music</option>
                                        <option value="v8_audio_tts">V8OS System Speech</option>
                                        <option value="tencent_hunyuan_3d">Tencent Hunyuan 3D</option>
                                        <option value="catalog_only">{t("app.admin.dashboard.model.hub.page.mediaAdapterCatalogOnly")}</option>
                                    </select>
                                    <p className="text-xs leading-5 text-muted-foreground">
                                        {t("app.admin.dashboard.model.hub.page.mediaAdapterHelp")}
                                    </p>
                                </div>}
                                {getMediaCapabilityOptions(modelType).length > 0 ? (
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between gap-3">
                                            <Label>{t("app.admin.dashboard.model.hub.capability.title")}</Label>
                                            <span className="text-[11px] text-muted-foreground">
                                                {t("app.admin.dashboard.model.hub.capability.selectedCount", { count: mediaCapabilityModes.length })}
                                            </span>
                                        </div>
                                        <div className="flex flex-wrap gap-1.5 rounded-lg border border-border/70 bg-muted/20 p-2">
                                            {getMediaCapabilityOptions(modelType).map((option) => {
                                                const checked = mediaCapabilityModes.includes(option.id);
                                                const isLastSelected = checked && mediaCapabilityModes.length === 1;
                                                return (
                                                    <label
                                                        key={option.id}
                                                        className={`inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors ${checked ? "border-primary/40 bg-primary/10 text-foreground" : "border-border/70 bg-background text-muted-foreground hover:text-foreground"} ${isLastSelected ? "cursor-not-allowed opacity-70" : "cursor-pointer"}`}
                                                    >
                                                        <Checkbox
                                                            checked={checked}
                                                            disabled={isLastSelected}
                                                            onCheckedChange={(nextChecked) => {
                                                                setMediaCapabilityModes((current) => nextChecked === true
                                                                    ? Array.from(new Set([...current, option.id]))
                                                                    : current.filter((item) => item !== option.id));
                                                            }}
                                                            className="h-3.5 w-3.5 rounded-[3px]"
                                                        />
                                                        <span>{t(option.labelKey)}</span>
                                                    </label>
                                                );
                                            })}
                                        </div>
                                        <input type="hidden" name="operationKind" value={deriveMediaOperationKinds(modelType, mediaCapabilityModes)[0] || ""} />
                                        <p className="text-xs leading-5 text-muted-foreground">
                                            {t("app.admin.dashboard.model.hub.capability.help")}
                                        </p>
                                    </div>
                                ) : null}
                                {modelType === "WORKFLOW" ? (
                                    <div className="space-y-4 rounded-lg border border-border/70 p-3">
                                        <div className="space-y-2">
                                            <Label htmlFor="comfy-workflow-file">{t("app.admin.dashboard.model.hub.comfy.apiWorkflow")}</Label>
                                            <Input
                                                id="comfy-workflow-file"
                                                type="file"
                                                accept="application/json,.json"
                                                onChange={async (event) => {
                                                    const file = event.target.files?.[0];
                                                    if (!file) return;
                                                    try {
                                                        const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
                                                        const prompt = parsed.prompt && typeof parsed.prompt === "object" ? parsed.prompt : parsed;
                                                        if (!prompt || Array.isArray(prompt) || typeof prompt !== "object") throw new Error("invalid");
                                                        setComfyWorkflow((current) => ({ ...current, promptJson: JSON.stringify(prompt, null, 2) }));
                                                    } catch {
                                                        toast({
                                                            variant: "destructive",
                                                            title: t("app.admin.dashboard.model.hub.comfy.workflowInvalidTitle"),
                                                            description: t("app.admin.dashboard.model.hub.comfy.workflowInvalid"),
                                                        });
                                                    }
                                                }}
                                            />
                                            <Textarea
                                                value={comfyWorkflow.promptJson}
                                                onChange={(event) => setComfyWorkflow((current) => ({ ...current, promptJson: event.target.value }))}
                                                className="max-h-48 min-h-24 font-mono text-xs"
                                                aria-label={t("app.admin.dashboard.model.hub.comfy.apiWorkflowJson")}
                                            />
                                        </div>
                                        <div className="grid gap-3 md:grid-cols-2">
                                            <Input value={comfyWorkflow.imageNodeId} onChange={(event) => setComfyWorkflow((current) => ({ ...current, imageNodeId: event.target.value }))} placeholder={t("app.admin.dashboard.model.hub.comfy.imageNodeId")} />
                                            <Input value={comfyWorkflow.imageInputName} onChange={(event) => setComfyWorkflow((current) => ({ ...current, imageInputName: event.target.value }))} placeholder={t("app.admin.dashboard.model.hub.comfy.imageInputName")} />
                                            <Input value={comfyWorkflow.videoNodeId} onChange={(event) => setComfyWorkflow((current) => ({ ...current, videoNodeId: event.target.value }))} placeholder={t("app.admin.dashboard.model.hub.comfy.videoNodeId")} />
                                            <Input value={comfyWorkflow.videoInputName} onChange={(event) => setComfyWorkflow((current) => ({ ...current, videoInputName: event.target.value }))} placeholder={t("app.admin.dashboard.model.hub.comfy.videoInputName")} />
                                            <Input value={comfyWorkflow.outputNodeId} onChange={(event) => setComfyWorkflow((current) => ({ ...current, outputNodeId: event.target.value }))} placeholder={t("app.admin.dashboard.model.hub.comfy.outputNodeId")} />
                                            <Input value={comfyWorkflow.outputField} onChange={(event) => setComfyWorkflow((current) => ({ ...current, outputField: event.target.value }))} placeholder={t("app.admin.dashboard.model.hub.comfy.outputField")} />
                                        </div>
                                    </div>
                                ) : null}
                                <p className="text-xs leading-5 text-muted-foreground">
                                    {t("app.admin.dashboard.model.hub.page.manualBindingHelp")}
                                </p>
                            </div>
                        ) : null}
                        <Button type="submit" className="w-full">{t("app.admin.dashboard.model.hub.page.kb7dfaded")}</Button>
                    </form>
                </DialogContent>
            </Dialog>
        </AdminPageShell>);
}
