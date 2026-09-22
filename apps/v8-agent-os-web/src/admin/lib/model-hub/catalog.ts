import { type ProviderChannel } from "@admin/lib/models/provider-admin";
import { createProviderChannel } from "@admin/lib/model-hub/channels";

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

export const CUSTOM_PROVIDER_CAPABILITIES: Array<{ id: CustomProviderCapability; labelKey: string }> = [
    { id: "text", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityText" },
    { id: "vision", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVision" },
    { id: "image", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityImage" },
    { id: "video", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVideo" },
    { id: "voice", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityVoice" },
    { id: "music", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityMusic" },
    { id: "model3d", labelKey: "app.admin.dashboard.model.hub.catalog.capabilityModel3d" },
];

export const CATALOG_PURPOSES: { id: CatalogPurpose; labelKey: string; hintKey: string; modelType: string; modality?: string }[] = [
    { id: "chat", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.chat", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.chatHint", modelType: "TEXT" },
    { id: "image", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.image", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.imageHint", modelType: "IMAGE", modality: "image" },
    { id: "video", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.video", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.videoHint", modelType: "VIDEO", modality: "video" },
    { id: "voice", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.voice", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.voiceHint", modelType: "VOICE", modality: "voice" },
    { id: "music", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.music", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.musicHint", modelType: "MUSIC", modality: "music" },
    { id: "workflow", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.workflow", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.workflowHint", modelType: "WORKFLOW", modality: "workflow" },
    { id: "model3d", labelKey: "app.admin.dashboard.model.hub.catalog.purpose.model3d", hintKey: "app.admin.dashboard.model.hub.catalog.purpose.model3dHint", modelType: "MODEL3D", modality: "model3d" },
];

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
        if (!provider.isCustom && provider.catalogVisibility === "internal_capability" && internalCapabilityIds.has(provider.id)) continue;
        if (provider.isCustom || !projected.has(provider.id)) projected.set(provider.id, provider);
    }
    return Array.from(projected.values());
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
