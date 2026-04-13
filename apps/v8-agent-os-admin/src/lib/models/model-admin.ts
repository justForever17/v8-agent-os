type EngineProviderMeta = {
    name?: string;
    icon?: string | null;
};

type EngineModelMeta = Record<string, unknown>;

export type EngineProviderContainer = {
    provider?: EngineProviderMeta;
    models?: Record<string, EngineModelMeta>;
};

export type AdminModelRecord = {
    id: string;
    providerId: string;
    modelId: string;
    name: string;
    type: string;
    contextWindow: number | null;
    maxTokens: number | null;
    temperature: number | null;
    rerankApiFlavor: string;
    isEnabled: boolean;
    provider: {
        name: string;
        icon?: string | null;
    };
};

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

    return {
        id: modelId,
        providerId,
        modelId,
        name: String(modelMeta.name || modelId),
        type: String(modelMeta.type || "TEXT"),
        contextWindow: asNullableNumber(modelMeta.contextWindow),
        maxTokens: asNullableNumber(modelMeta.maxTokens),
        temperature: asNullableNumber(modelMeta.temperature),
        rerankApiFlavor: String(modelMeta.rerank_api_flavor || modelMeta.rerankApiFlavor || ""),
        isEnabled: modelMeta.isEnabled !== false,
        provider: {
            name: String(providerMeta.name || providerId),
            icon: providerMeta.icon || null,
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
    return {
        name: data.name,
        type: data.type,
        contextWindow: parseOptionalInteger(data.contextWindow),
        maxTokens: parseOptionalInteger(data.maxTokens),
        temperature: parseOptionalFloat(data.temperature),
        costPerInput: parseOptionalFloat(data.costPerInput),
        costPerOutput: parseOptionalFloat(data.costPerOutput),
        rerank_api_flavor: String(data.rerankApiFlavor || "").trim() || undefined,
    };
}
