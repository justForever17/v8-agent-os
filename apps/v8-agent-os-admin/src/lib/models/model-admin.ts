import { deriveMediaOperationKinds, resolveMediaCapabilityModes } from "@/lib/models/media-capabilities";

type EngineProviderMeta = {
    name?: string;
    icon?: string | null;
    logoAsset?: string | null;
    base_url?: string | null;
    baseUrl?: string | null;
};

type EngineModelMeta = Record<string, unknown>;

export type EngineProviderContainer = {
    provider?: EngineProviderMeta;
    models?: Record<string, EngineModelMeta>;
};

export type AdminModelRecord = {
    id: string;
    modelRef: string;
    providerId: string;
    modelId: string;
    type: string;
    contextWindow: number | null;
    maxTokens: number | null;
    rerankApiFlavor: string;
    thinkingControl?: Record<string, unknown> | null;
    operationKinds?: string[];
    mediaLimits?: Record<string, unknown> | null;
    endpointBinding?: Record<string, unknown> | null;
    logoAsset?: string | null;
    isEnabled: boolean;
    provider: {
        id?: string;
        name: string;
        icon?: string | null;
        logoAsset?: string | null;
        baseUrl?: string | null;
    };
};

export function buildModelRef(providerId: string, modelId: string) {
    const provider = String(providerId || "").trim();
    const model = String(modelId || "").trim();
    if (!provider || !model) return "";
    return `${provider}::${encodeURIComponent(model)}`;
}

export function parseModelRef(value: string): { providerId: string; modelId: string } | null {
    const raw = String(value || "").trim();
    if (!raw.includes("::")) return null;
    const [providerId, encodedModelId] = raw.split("::", 2);
    if (!providerId || !encodedModelId) return null;
    return { providerId, modelId: decodeURIComponent(encodedModelId) };
}

function asNullableNumber(value: unknown) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function parseOptionalInteger(value: unknown) {
    const raw = String(value ?? "").trim();
    if (!raw) return undefined;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : undefined;
}

export function parseOptionalFloat(value: unknown) {
    const raw = String(value ?? "").trim();
    if (!raw) return undefined;
    const parsed = Number.parseFloat(raw);
    return Number.isFinite(parsed) ? parsed : undefined;
}

export function mapEngineModel(
    providerId: string,
    providerData: EngineProviderContainer,
    modelId: string,
): AdminModelRecord {
    const providerMeta = providerData.provider || {};
    const rawModelMeta = providerData.models?.[modelId];
    const modelMeta = (rawModelMeta && typeof rawModelMeta === "object")
        ? rawModelMeta
        : {};

    const modelRef = buildModelRef(providerId, modelId);
    return {
        id: modelRef,
        modelRef,
        providerId,
        modelId,
        type: String(modelMeta.type || "TEXT"),
        contextWindow: asNullableNumber(modelMeta.contextWindow),
        maxTokens: asNullableNumber(modelMeta.maxTokens),
        rerankApiFlavor: String(modelMeta.rerank_api_flavor || modelMeta.rerankApiFlavor || ""),
        thinkingControl: modelMeta.thinkingControl && typeof modelMeta.thinkingControl === "object"
            ? modelMeta.thinkingControl as Record<string, unknown>
            : null,
        operationKinds: Array.isArray(modelMeta.operationKinds)
            ? modelMeta.operationKinds.filter((item): item is string => typeof item === "string")
            : undefined,
        mediaLimits: modelMeta.mediaLimits && typeof modelMeta.mediaLimits === "object"
            ? modelMeta.mediaLimits as Record<string, unknown>
            : null,
        endpointBinding: modelMeta.endpointBinding && typeof modelMeta.endpointBinding === "object"
            ? modelMeta.endpointBinding as Record<string, unknown>
            : null,
        logoAsset: String(modelMeta.logoAsset || "") || null,
        isEnabled: modelMeta.isEnabled !== false,
        provider: {
            id: providerId,
            name: String(providerMeta.name || providerId),
            icon: providerMeta.icon || null,
            logoAsset: providerMeta.logoAsset || null,
            baseUrl: String(providerMeta.base_url || providerMeta.baseUrl || "") || null,
        },
    };
}

export function listEngineModels(
    providers: Record<string, EngineProviderContainer>,
    providerIdFilter?: string | null,
) {
    const models: AdminModelRecord[] = [];

    for (const providerId of Object.keys(providers || {})) {
        if (providerIdFilter && providerId !== providerIdFilter) {
            continue;
        }

        const providerData = providers[providerId] || {};
        for (const modelId of Object.keys(providerData.models || {})) {
            models.push(mapEngineModel(providerId, providerData, modelId));
        }
    }

    return models;
}

export function buildModelMutationPayload(data: Record<string, unknown>) {
    const existingMediaLimits = data.mediaLimits && typeof data.mediaLimits === "object"
        ? data.mediaLimits as Record<string, unknown>
        : {};
    const capabilityModesProvided = Object.prototype.hasOwnProperty.call(data, "capabilityModes");
    const capabilityModes = capabilityModesProvided
        ? resolveMediaCapabilityModes(String(data.type || ""), data.capabilityModes, [])
        : undefined;
    const derivedOperationKinds = capabilityModesProvided
        ? deriveMediaOperationKinds(String(data.type || ""), capabilityModes)
        : undefined;
    const payload: Record<string, unknown> = {
        type: data.type,
        contextWindow: parseOptionalInteger(data.contextWindow),
        maxTokens: parseOptionalInteger(data.maxTokens),
        costPerInput: parseOptionalFloat(data.costPerInput),
        costPerOutput: parseOptionalFloat(data.costPerOutput),
        rerank_api_flavor: String(data.rerankApiFlavor || "").trim() || undefined,
    };
    if (data.thinkingControl && typeof data.thinkingControl === "object") {
        payload.thinkingControl = data.thinkingControl;
    }
    if (capabilityModesProvided) {
        payload.operationKinds = derivedOperationKinds;
        payload.mediaLimits = {
            ...existingMediaLimits,
            capabilityModes,
            operationKinds: derivedOperationKinds,
        };
    }
    const existingBinding = data.endpointBinding && typeof data.endpointBinding === "object"
        ? data.endpointBinding as Record<string, unknown>
        : {};
    const wireProtocol = data.wireProtocol === undefined
        ? String(existingBinding.wireProtocol || "").trim()
        : String(data.wireProtocol || "").trim();
    const protocolFieldPresent = Object.prototype.hasOwnProperty.call(data, "wireProtocol");
    const protocolEndpointPaths: Record<string, string> = {
        "openai.chat_completions": "chat/completions",
        "openai.responses": "responses",
        "anthropic.messages": "messages",
        "gemini.generate_content": "models/{model}:generateContent",
    };
    let endpointPath = data.endpointPath === undefined
        ? String(existingBinding.endpointPath || "").trim().replace(/^\/+|\/+$/g, "")
        : String(data.endpointPath || "").trim().replace(/^\/+|\/+$/g, "");
    if (protocolFieldPresent) {
        endpointPath = wireProtocol ? (protocolEndpointPaths[wireProtocol] || endpointPath) : "";
    }
    const providerModelId = data.providerModelId === undefined
        ? String(existingBinding.providerModelId || "").trim().replace(/^\/+|\/+$/g, "")
        : String(data.providerModelId || "").trim().replace(/^\/+|\/+$/g, "");
    const operationKind = capabilityModesProvided
        ? String(derivedOperationKinds?.[0] || "")
        : data.operationKind === undefined
        ? String(existingBinding.operationKind || "").trim()
        : String(data.operationKind || "").trim();
    const adapter = data.adapter === undefined
        ? String(existingBinding.adapter || "").trim()
        : String(data.adapter || "").trim();
    if (endpointPath || providerModelId || operationKind || adapter || wireProtocol || protocolFieldPresent || capabilityModesProvided) {
        payload.endpointBinding = {
            ...existingBinding,
            endpointPath,
            providerModelId,
            operationKind,
            adapter,
            wireProtocol,
            protocolConfidence: wireProtocol ? "authoritative" : "",
            protocolSource: wireProtocol ? "manual" : "",
            protocolSourceRefs: wireProtocol ? (existingBinding.protocolSourceRefs || []) : [],
            protocolWarning: "",
            provenance: { source: "manual", confidence: "authoritative" },
        };
    }
    return payload;
}
