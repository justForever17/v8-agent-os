import { readJson, writeJson } from "@/lib/storage";

type AgentBindingRecord = {
    model_id?: string;
    modelId?: string;
};

type ModelsConfig = {
    version?: number;
    providers?: Record<string, unknown>;
    roles?: Record<string, string>;
    bindings?: {
        agents?: Record<string, AgentBindingRecord | string>;
    };
    governance?: Record<string, unknown>;
    routingPolicies?: Record<string, unknown>;
};

type SettingsJson = {
    settings?: { key: string; value: string }[];
    vision_model_id?: string;
};

type MemoryConfigJson = {
    model_id?: string;
    extraction_model?: string;
    extraction_temperature?: number;
    temperature?: number;
    embedding_model?: string;
    reranker_model?: string;
};

type ContextConfigJson = {
    compression?: {
        summary_model?: string;
    };
};

type SupervisorConfigJson = {
    model_id?: string | null;
    allowed_tools?: string[] | null;
};

const DEFAULT_MODELS_CONFIG: ModelsConfig = {
    version: 2,
    providers: {},
    roles: {},
    bindings: {
        agents: {},
    },
    governance: {},
    routingPolicies: {},
};

function pruneLegacySettingsKey(settings: SettingsJson, key: string): boolean {
    const original = settings.settings || [];
    const next = original.filter((item) => item.key !== key);
    if (next.length !== original.length) {
        settings.settings = next;
        return true;
    }
    return false;
}

function migrateLegacyRoleBindings(config: ModelsConfig): ModelsConfig {
    const nextConfig: ModelsConfig = {
        ...config,
        roles: {
            ...(config.roles || {}),
        },
        bindings: {
            ...(config.bindings || {}),
            agents: {
                ...((config.bindings || {}).agents || {}),
            },
        },
    };

    let modelsChanged = false;

    const assignRoleIfMissing = (role: string, value: unknown) => {
        const normalized = normalizeModelId(value);
        if (!normalized) return;
        if (normalizeModelId(nextConfig.roles?.[role])) return;
        nextConfig.roles = {
            ...(nextConfig.roles || {}),
            [role]: normalized,
        };
        modelsChanged = true;
    };

    const settingsData = readJson<SettingsJson>("settings.json", { settings: [] });
    let settingsChanged = false;
    const defaultAgentModel = settingsData.settings?.find((item) => item.key === "DEFAULT_AGENT_MODEL_ID")?.value;
    assignRoleIfMissing("default", defaultAgentModel);
    settingsChanged = pruneLegacySettingsKey(settingsData, "DEFAULT_AGENT_MODEL_ID") || settingsChanged;

    const legacyVisionModel = settingsData.settings?.find((item) => item.key === "VISION_MODEL_ID")?.value || settingsData.vision_model_id;
    assignRoleIfMissing("vision", legacyVisionModel);
    settingsChanged = pruneLegacySettingsKey(settingsData, "VISION_MODEL_ID") || settingsChanged;
    if (Object.prototype.hasOwnProperty.call(settingsData, "vision_model_id")) {
        delete settingsData.vision_model_id;
        settingsChanged = true;
    }
    if (settingsChanged) {
        writeJson("settings.json", {
            ...settingsData,
            settings: settingsData.settings || [],
        });
    }

    const memoryConfig = readJson<MemoryConfigJson>("memory_config.json", {});
    let memoryChanged = false;
    assignRoleIfMissing("extraction", memoryConfig.extraction_model || memoryConfig.model_id);
    assignRoleIfMissing("embedding", memoryConfig.embedding_model);
    assignRoleIfMissing("reranker", memoryConfig.reranker_model);
    if (memoryConfig.temperature !== undefined && memoryConfig.extraction_temperature === undefined) {
        memoryConfig.extraction_temperature = memoryConfig.temperature;
        memoryChanged = true;
    }
    for (const key of ["model_id", "extraction_model", "embedding_model", "reranker_model", "temperature"] as const) {
        if (Object.prototype.hasOwnProperty.call(memoryConfig, key)) {
            delete memoryConfig[key];
            memoryChanged = true;
        }
    }
    if (memoryChanged) {
        writeJson("memory_config.json", memoryConfig);
    }

    const contextConfig = readJson<ContextConfigJson>("context_config.json", {});
    const compression = { ...(contextConfig.compression || {}) };
    let contextChanged = false;
    assignRoleIfMissing("summary", compression.summary_model);
    if (Object.prototype.hasOwnProperty.call(compression, "summary_model")) {
        delete compression.summary_model;
        contextChanged = true;
    }
    if (contextChanged) {
        writeJson("context_config.json", {
            ...contextConfig,
            compression,
        });
    }

    const supervisorConfig = readJson<SupervisorConfigJson>("supervisor_config.json", {});
    let supervisorChanged = false;
    assignRoleIfMissing("supervisor", supervisorConfig.model_id);
    if (Object.prototype.hasOwnProperty.call(supervisorConfig, "model_id")) {
        delete supervisorConfig.model_id;
        supervisorChanged = true;
    }
    if (supervisorChanged) {
        writeJson("supervisor_config.json", supervisorConfig);
    }

    if (modelsChanged) {
        writeJson("models.json", nextConfig);
    }

    return nextConfig;
}

function normalizeModelId(value: unknown): string {
    const normalized = String(value ?? "").trim();
    if (!normalized || normalized === "__empty__" || normalized === "none") {
        return "";
    }
    return normalized;
}

export function readModelsConfig(): ModelsConfig {
    const config = readJson<ModelsConfig>("models.json", DEFAULT_MODELS_CONFIG);
    const migrated = migrateLegacyRoleBindings({
        ...DEFAULT_MODELS_CONFIG,
        ...config,
        roles: {
            ...(DEFAULT_MODELS_CONFIG.roles || {}),
            ...(config.roles || {}),
        },
        bindings: {
            agents: {
                ...((DEFAULT_MODELS_CONFIG.bindings || {}).agents || {}),
                ...((config.bindings || {}).agents || {}),
            },
        },
    });
    return {
        ...DEFAULT_MODELS_CONFIG,
        ...migrated,
        roles: {
            ...(DEFAULT_MODELS_CONFIG.roles || {}),
            ...(migrated.roles || {}),
        },
        bindings: {
            agents: {
                ...((DEFAULT_MODELS_CONFIG.bindings || {}).agents || {}),
                ...((migrated.bindings || {}).agents || {}),
            },
        },
    };
}

export function writeModelsConfig(config: ModelsConfig) {
    writeJson("models.json", config);
}

export function getRoleModelBinding(role: string): string | null {
    const config = readModelsConfig();
    const modelId = normalizeModelId(config.roles?.[role]);
    return modelId || null;
}

export function setRoleModelBinding(role: string, value: unknown): string | null {
    const config = readModelsConfig();
    const modelId = normalizeModelId(value);
    config.roles = {
        ...(config.roles || {}),
        [role]: modelId,
    };
    writeModelsConfig(config);
    return modelId || null;
}

export function getAgentModelBinding(agentId: string): string | null {
    const config = readModelsConfig();
    const record = config.bindings?.agents?.[agentId];
    if (typeof record === "string") {
        return normalizeModelId(record) || null;
    }
    return normalizeModelId(record?.model_id || record?.modelId) || null;
}

export function setAgentModelBinding(agentId: string, value: unknown): string | null {
    const config = readModelsConfig();
    const modelId = normalizeModelId(value);
    const nextAgents = {
        ...(config.bindings?.agents || {}),
    };

    if (modelId) {
        nextAgents[agentId] = { model_id: modelId };
    } else {
        delete nextAgents[agentId];
    }

    config.bindings = {
        ...(config.bindings || {}),
        agents: nextAgents,
    };

    writeModelsConfig(config);
    return modelId || null;
}
