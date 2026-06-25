type EngineProviderMeta = {
    name?: string;
    icon?: string | null;
    logoAsset?: string | null;
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
    logoAsset?: string | null;
    isEnabled: boolean;
    provider: {
        id?: string;
        name: string;
        icon?: string | null;
        logoAsset?: string | null;
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
        logoAsset: String(modelMeta.logoAsset || "") || null,
        isEnabled: modelMeta.isEnabled !== false,
        provider: {
            id: providerId,
            name: String(providerMeta.name || providerId),
            icon: providerMeta.icon || null,
            logoAsset: providerMeta.logoAsset || null,
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
    return payload;
}
