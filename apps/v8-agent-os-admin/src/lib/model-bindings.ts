import fs from "fs";
import path from "path";

import { getBaseDir } from "@/lib/storage";

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

type CanonicalConfig = {
    models?: ModelsConfig;
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

function normalizeModelId(value: unknown): string {
    const normalized = String(value ?? "").trim();
    if (!normalized || normalized === "__empty__" || normalized === "none") {
        return "";
    }
    return normalized;
}

function readCanonicalConfig(): CanonicalConfig {
    try {
        const filePath = path.join(getBaseDir(), "config.json");
        if (!fs.existsSync(filePath)) {
            return {};
        }
        const raw = fs.readFileSync(filePath, "utf-8");
        const parsed = JSON.parse(raw) as CanonicalConfig;
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
        console.error("Failed to read config.json:", error);
        return {};
    }
}

function writeCanonicalConfig(config: CanonicalConfig) {
    try {
        const filePath = path.join(getBaseDir(), "config.json");
        fs.writeFileSync(filePath, JSON.stringify(config, null, 2), "utf-8");
    } catch (error) {
        console.error("Failed to write config.json:", error);
    }
}

export function readModelsConfig(): ModelsConfig {
    const config = readCanonicalConfig();
    const models = config.models || {};
    return {
        ...DEFAULT_MODELS_CONFIG,
        ...models,
        roles: {
            ...(DEFAULT_MODELS_CONFIG.roles || {}),
            ...(models.roles || {}),
        },
        bindings: {
            agents: {
                ...((DEFAULT_MODELS_CONFIG.bindings || {}).agents || {}),
                ...((models.bindings || {}).agents || {}),
            },
        },
    };
}

export function writeModelsConfig(config: ModelsConfig) {
    const canonical = readCanonicalConfig();
    canonical.models = config;
    writeCanonicalConfig(canonical);
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
