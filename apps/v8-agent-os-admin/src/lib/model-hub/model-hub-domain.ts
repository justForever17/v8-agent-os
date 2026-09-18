import type { ControlPlaneModel, ProviderOverview } from "@/components/models/control-plane-types";
import type { ConfigRegistryEnvelope } from "@/lib/config-registry";
import audioVoicePresets from "@/lib/models/audio-voice-presets.json";
import type {
    LocalBackendPreset,
    PlatformLoginPreset,
    ProviderChannel,
} from "@/lib/models/provider-admin";

export type AIProvider = {
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
export type AIModel = {
    id: string;
    modelRef?: string;
    providerId: string;
    modelId: string;
    type: string;
    contextWindow?: number | null;
    maxTokens?: number | null;
    outputTokenMode?: "auto" | "fixed";
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
export type ModelHubPayload = {
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
export type ModelConnectionStatus = {
    status: "idle" | "testing" | "success" | "warning" | "error";
    message?: string;
};
export type ModelReasoningRepairStatus = {
    status: "idle" | "repairing" | "success" | "warning" | "error";
    message?: string;
};
export type CatalogModel = {
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
export type CatalogProvider = {
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
export type CatalogPurpose = "chat" | "image" | "video" | "voice" | "music" | "workflow" | "model3d";
export type CustomProviderCapability = "text" | "vision" | "image" | "video" | "voice" | "music" | "model3d";
export type CatalogRuntimeProtocol = string;
export type ModelWireProtocol = "" | "openai.chat_completions" | "openai.responses" | "anthropic.messages" | "gemini.generate_content";
export type ComfyWorkflowDraft = {
    promptJson: string;
    imageNodeId: string;
    imageInputName: string;
    videoNodeId: string;
    videoInputName: string;
    outputNodeId: string;
    outputField: string;
};

export const EMPTY_COMFY_WORKFLOW: ComfyWorkflowDraft = {
    promptJson: "",
    imageNodeId: "",
    imageInputName: "",
    videoNodeId: "",
    videoInputName: "",
    outputNodeId: "",
    outputField: "",
};

export function comfyWorkflowDraft(mediaLimits: Record<string, unknown> | null | undefined): ComfyWorkflowDraft {
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

export const MODEL_WIRE_PROTOCOLS: Array<{ id: Exclude<ModelWireProtocol, "">; labelKey: string }> = [
    { id: "openai.chat_completions", labelKey: "app.admin.dashboard.model.hub.protocol.openaiChatCompletions" },
    { id: "openai.responses", labelKey: "app.admin.dashboard.model.hub.protocol.openaiResponses" },
    { id: "anthropic.messages", labelKey: "app.admin.dashboard.model.hub.protocol.anthropicMessages" },
    { id: "gemini.generate_content", labelKey: "app.admin.dashboard.model.hub.protocol.geminiGenerateContent" },
];

export const PROVIDER_CHANNEL_PRESETS: Array<{
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

export function createProviderChannel(apiStandard: string, baseUrl = "", id?: string): ProviderChannel {
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

export function editableProviderChannels(provider: AIProvider | null, apiStandard: string, baseUrl: string): ProviderChannel[] {
    const configured = (provider?.channels || []).filter((channel) => channel.source !== "legacy_projection");
    if (configured.length > 0) return configured.map((channel) => ({ ...channel, wireProtocols: [...channel.wireProtocols] }));
    return [createProviderChannel(apiStandard, baseUrl, "default")];
}

export const CUSTOM_PROVIDER_CAPABILITIES: Array<{ id: CustomProviderCapability; labelKey: string }> = [
    { id: "text", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityText" },
    { id: "vision", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVision" },
    { id: "image", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityImage" },
    { id: "video", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVideo" },
    { id: "voice", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVoice" },
    { id: "music", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityMusic" },
    { id: "model3d", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityModel3d" },
];
export type AudioRuntimeConfig = {
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

export type ModelHubBootstrapPayload = {
    providers?: AIProvider[];
    models?: AIModel[];
    hubEnvelope?: ConfigRegistryEnvelope<ModelHubPayload> | null;
    defaultModel?: { modelRef?: string | null; modelId?: string | null; value?: string | null };
    catalog?: { providers?: CatalogProvider[] };
    audioConfig?: unknown;
};
export const MODEL_HUB_BOOTSTRAP_URL = "/api/model-hub/bootstrap";
export type AudioVoicePreset = {
    value: string;
    label?: string;
    labelKey?: string;
};
export type AudioVoiceOption = {
    value: string;
    label: string;
    group?: string;
    deletable?: boolean;
    source?: "remote" | "preset" | "local_ledger" | string;
    availability?: "available" | "confirmed" | "pending_activation" | string;
};
export type TtsVoiceCapabilities = {
    clone?: boolean;
    design?: boolean;
    list?: boolean;
    delete?: boolean;
    preview?: boolean;
    commit?: boolean;
};
export type TtsVoiceAssetPolicy = {
    assetScope?: "durable_remote" | "provider_slot" | "ephemeral_request" | "qualification_only" | string;
    inventorySource?: "remote" | "local_projection" | "none" | string;
    designFlow?: "direct" | "ephemeral" | "preview_then_commit" | "qualification_only" | string;
    eligibilityStatus?: "available" | "eligible" | "requires_approval" | string;
    consentRequired?: boolean;
    docsUrl?: string;
    applicationUrl?: string;
};
export type TtsVoiceDesignCandidate = {
    generatedVoiceId: string;
    previewAudio: string;
};
export type TtsVoiceTextConstraint = {
    required?: boolean;
    minChars?: number;
    maxChars?: number | null;
};
export type TtsVoiceDesignConstraints = {
    prompt?: TtsVoiceTextConstraint;
    previewText?: TtsVoiceTextConstraint;
    voiceId?: TtsVoiceTextConstraint & {
        role?: "none" | "custom_id" | "prefix" | "provider_slot" | string;
        format?: string;
    };
};
export type TtsVoiceProviderInfo = {
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
export type AudioVoicePresetProvider = {
    match?: string[];
    protocol?: string;
    defaultEndpoint?: string;
    voices?: AudioVoicePreset[];
    supportsRemoteVoiceList?: boolean;
    remoteVoiceListPath?: string;
};
export type AudioVoicePresetTable = {
    providers?: Record<string, AudioVoicePresetProvider>;
};

export const CATALOG_PURPOSES: { id: CatalogPurpose; labelKey: string; hintKey: string; modelType: string; modality?: string }[] = [
    { id: "chat", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.chat", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.chatHint", modelType: "TEXT" },
    { id: "image", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.image", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.imageHint", modelType: "IMAGE", modality: "image" },
    { id: "video", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.video", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.videoHint", modelType: "VIDEO", modality: "video" },
    { id: "voice", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.voice", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.voiceHint", modelType: "VOICE", modality: "voice" },
    { id: "music", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.music", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.musicHint", modelType: "MUSIC", modality: "music" },
    { id: "workflow", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.workflow", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.workflowHint", modelType: "WORKFLOW", modality: "workflow" },
    { id: "model3d", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.model3d", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.model3dHint", modelType: "MODEL3D", modality: "model3d" },
];

export const MEDIA_MODEL_TYPES = new Set<string>(["MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"]);
export const RETRIEVAL_MODEL_TYPES = new Set<string>(["EMBEDDING", "RERANK", "RERANKER"]);
export const TTS_MODEL_TYPES = new Set<string>(["AUDIO", "VOICE"]);
export const DEFAULT_AUDIO_CONFIG: AudioRuntimeConfig = {
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
export const AUDIO_VOICE_PRESET_TABLE = audioVoicePresets as AudioVoicePresetTable;

export function localizeAudioVoicePresets(key: string, t: (key: string) => string): { value: string; label: string }[] {
    const provider = AUDIO_VOICE_PRESET_TABLE.providers?.[key];
    return (provider?.voices || []).map((voice) => ({
        value: voice.value,
        label: voice.labelKey ? t(voice.labelKey) : voice.label || voice.value,
    }));
}

export function resolveAudioVoicePresetKey(modelRef: string): string {
    const normalized = modelRef.toLowerCase();
    for (const [key, provider] of Object.entries(AUDIO_VOICE_PRESET_TABLE.providers || {})) {
        if (key === "edge-tts") continue;
        if ((provider.match || []).some((pattern) => normalized.includes(pattern.toLowerCase()))) {
            return key;
        }
    }
    return "";
}

export function voicePresetsForCustomTtsProtocol(protocol: string, t: (key: string) => string): { value: string; label: string }[] {
    if (protocol === "openai_speech") return localizeAudioVoicePresets("openai", t);
    if (protocol === "minimax_t2a_v2") return localizeAudioVoicePresets("minimax", t);
    return [];
}

export function headerValueForInput(value: string | Record<string, string> | undefined): string {
    if (!value) return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value);
}

export function sttEndpointPlaceholder(protocol: string): string {
    if (protocol === "openai_transcription") return "https://api.openai.com/v1/audio/transcriptions";
    if (protocol === "json_base64") return "https://example.com/transcribe-json";
    return "https://example.com/transcribe";
}

export function ttsEndpointPlaceholder(protocol: string): string {
    if (protocol === "openai_speech") return "https://api.openai.com/v1/audio/speech";
    if (protocol === "minimax_t2a_v2") return "https://api.minimaxi.com/v1/t2a_v2";
    return "https://example.com/tts";
}

export function mergeAudioConfig(value: unknown): AudioRuntimeConfig {
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

export function modelRefFor(model: AIModel): string {
    return model.modelRef || `${model.providerId}::${model.modelId || model.id}`;
}

export function modelLabel(model: AIModel): string {
    return `${model.provider?.name || model.providerId} 路 ${model.modelId || model.id}`;
}

export function hasAudioInputCapability(model: AIModel): boolean {
    const ref = `${modelRefFor(model)} ${model.modelId || ""}`.toLowerCase();
    return ref.includes("stt")
        || ref.includes("asr")
        || ref.includes("whisper")
        || ref.includes("transcribe")
        || ref.includes("speech-to-text")
        || ref.includes("live-audio");
}

export function hasAudioOutputCapability(model: AIModel, controlMeta?: ControlPlaneModel | null): boolean {
    const type = normalizeModelType(model.type || controlMeta?.type);
    const capabilities = controlMeta?.capabilities;
    const ref = `${modelRefFor(model)} ${model.modelId || ""}`.toLowerCase();
    return TTS_MODEL_TYPES.has(type) || Boolean(capabilities?.audio || capabilities?.voice) || ref.includes("tts") || ref.includes("live-audio");
}

export function previewModelsUrl(provider?: CatalogProvider | null): string {
    if (!provider?.baseUrl) return "";
    if (provider.modelsUrl) return provider.modelsUrl;
    const path = provider.modelsPath || "/models";
    return `${provider.baseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

export function catalogProviderChannels(provider?: CatalogProvider | null): ProviderChannel[] {
    if (!provider) return [];
    if (provider.channels?.length) return provider.channels;
    const primary = createProviderChannel(provider.apiStandard || "openai", provider.baseUrl || "", "default");
    const channels = [primary];
    if (provider.anthropicCompatible?.baseUrl) {
        channels.push(createProviderChannel("anthropic", provider.anthropicCompatible.baseUrl, "anthropic"));
    }
    return channels;
}

export function isXiaomiAnthropicBaseUrl(value: string | null | undefined) {
    const normalized = String(value || "").trim().toLowerCase().replace(/\/+$/, "");
    return normalized.includes("xiaomimimo.com/anthropic") || normalized.includes("token-plan-cn.xiaomimimo.com/anthropic");
}

export function getCatalogPurposeConfig(purpose: CatalogPurpose) {
    return CATALOG_PURPOSES.find((item) => item.id === purpose) || CATALOG_PURPOSES[0];
}

export function urlPath(value: string | undefined) {
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

export function mediaRelativeSubmitPath(rootBaseUrl: string | undefined, sourceBaseUrl: string | undefined, submitPath: string | undefined) {
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

export function endpointMediaCatalogModel(model: CatalogModel, sourceProvider: CatalogProvider, rootProvider: CatalogProvider): CatalogModel {
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

export function buildCatalogProvidersForPurpose(catalogProviders: CatalogProvider[], purpose: CatalogPurpose): CatalogProvider[] {
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

export function normalizeModelType(value: string | null | undefined) {
    return String(value || "").trim().toUpperCase();
}

export function modelMatchesTab(model: AIModel, tab: string) {
    if (tab === "all") return true;
    const type = normalizeModelType(model.type);
    if (tab === "media") return MEDIA_MODEL_TYPES.has(type);
    if (tab === "voice") return type === "VOICE" || type === "AUDIO";
    if (tab === "model3d") return type === "MODEL3D";
    return type.toLowerCase() === tab;
}

export function preserveModelOrder(current: AIModel[], incoming: AIModel[]): AIModel[] {
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

export function providerMatchesPurpose(provider: CatalogProvider, purpose: CatalogPurpose) {
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

export function getModelTypeForPurpose(purpose: CatalogPurpose) {
    return getCatalogPurposeConfig(purpose).modelType;
}
export function extractErrorText(value: unknown, fallback: string): string {
    if (typeof value === "string" && value.trim())
        return value;
    if (Array.isArray(value)) {
        const joined = value
            .map((item) => extractErrorText(item, ""))
            .filter(Boolean)
            .join(" 路 ");
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
export function voiceManagerErrorText(payload: Record<string, unknown>, fallback: string): string {
    const message = extractErrorText(payload.error, fallback);
    const providerCode = typeof payload.providerCode === "string" ? payload.providerCode.trim() : "";
    const traceId = typeof payload.traceId === "string" ? payload.traceId.trim() : "";
    return [message, providerCode ? `code ${providerCode}` : "", traceId ? `trace ${traceId}` : ""]
        .filter(Boolean)
        .join(" 路 ");
}
export async function readJsonErrorMessage(response: Response, fallback: string) {
    const data = await response.json().catch(() => null);
    const detail = extractErrorText(data?.detail, "") || extractErrorText(data?.error, "");
    return detail || fallback;
}
