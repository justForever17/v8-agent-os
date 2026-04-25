export type ModelCapabilities = {
    chat: boolean;
    reasoning: boolean;
    toolCalling: boolean;
    vision: boolean;
    multimodal: boolean;
    streaming: boolean;
    image: boolean;
    video: boolean;
    audio: boolean;
    embedding: boolean;
    rerank: boolean;
    workflow: boolean;
    computerUse: boolean;
};

export type ControlPlaneModel = {
    id: string;
    modelRef?: string;
    modelId: string;
    providerId: string;
    providerName: string;
    providerIcon?: string | null;
    type: string;
    capabilityClass: string;
    priority: number;
    stabilityTier: string;
    contextWindow?: number | null;
    maxTokens?: number | null;
    capabilitySource?: string | null;
    parameterProfile?: string | null;
    mediaLimits?: Record<string, unknown>;
    isEnabled: boolean;
    capabilities: ModelCapabilities;
    capabilityTags: string[];
    assignedRoles: string[];
};

export type ControlPlaneRoleCard = {
    key: string;
    label: string;
    description: string;
    group: "system" | "extension";
    capabilityClasses: string[];
    rawModelId: string;
    resolvedModelId: string;
    resolvedModelRef?: string;
    resolvedProviderName: string;
    bindingState: "explicit" | "default" | "inherited_default" | "invalid" | "ambiguous" | "unbound";
    compatibleModels: Array<{
        modelId: string;
        modelRef?: string;
        providerName: string;
        capabilityClass: string;
        capabilityTags: string[];
    }>;
};

export type ControlPlaneModuleStatus = {
    key: string;
    label: string;
    description: string;
    group: "system" | "extension";
    status: "healthy" | "fallback" | "attention" | "planned";
    pagePath: string;
    pageLabel: string;
    resolvedModels: Array<{
        role: string;
        roleLabel: string;
        bindingState: string;
        modelId: string;
        modelRef?: string;
        providerName: string;
    }>;
};

export type ProviderOverview = {
    providerId: string;
    name: string;
    icon?: string | null;
    status: "healthy" | "attention" | "disabled";
    reason: string;
    assignedRoles: string[];
    models: number;
    enabledModels: number;
    apiStandard: string;
    type: string;
    events: number;
    successCount: number;
    errorCount: number;
    errorRate: number;
    avgLatencyMs: number;
    lastSeenAt?: string | null;
    circuitState: "closed" | "half_open" | "open";
    localCapabilityProbe?: {
        status: "supported" | "unsupported" | "unknown" | "not_applicable";
        message: string;
        modelId?: string | null;
        endpoint?: string | null;
        visionSupported?: boolean | null;
        contextLength?: number | null;
        maxContextLength?: number | null;
        params?: string | null;
    } | null;
};

export type GovernanceBudgetConfig = {
    enabled: boolean;
    globalDailyCostLimit: number;
    globalDailyTokenLimit: number;
    runMaxCost: number;
    runMaxTokens: number;
    defaultProjectDailyCostLimit: number;
    defaultProjectDailyTokenLimit: number;
    projectBudgets: Array<{
        projectId: string;
        projectName: string;
        dailyCostLimit: number;
        dailyTokenLimit: number;
    }>;
};

export type ControlPlaneConfig = {
    version: number;
    roles: Record<string, string>;
    governance: {
        enabled: boolean;
        stickyRunModel: boolean;
        allowSameCapabilityFailover: boolean;
        strictCapabilityMatch: boolean;
        maxLocalRetries: number;
        maxProviderSwitches: number;
        defaultStreaming: boolean;
        providerHealthWindowDays: number;
        providerFailureThreshold: number;
        providerErrorRateThreshold: number;
        budgets: GovernanceBudgetConfig;
    };
    routingPolicies: Record<string, string>;
    providers: Record<string, unknown>;
};

export type ControlPlanePayload = {
    config: ControlPlaneConfig;
    summary: {
        providers: number;
        enabledProviders: number;
        models: number;
        reasoningModels: number;
        multimodalModels: number;
        rolesAssigned: number;
        capabilityClasses: Record<string, number>;
    };
    models: ControlPlaneModel[];
    roles: ControlPlaneRoleCard[];
    modules: ControlPlaneModuleStatus[];
    providersOverview: ProviderOverview[];
    governanceSummary?: {
        budgets: {
            enabled: boolean;
            today: {
                costTotal: number;
                totalTokens: number;
                invocations: number;
            };
            limits: {
                globalDailyCostLimit: number;
                globalDailyTokenLimit: number;
                runMaxCost: number;
                runMaxTokens: number;
                defaultProjectDailyCostLimit: number;
                defaultProjectDailyTokenLimit: number;
            };
            projectOverrides: number;
        };
        failover: {
            enabled: boolean;
            strictCapabilityMatch: boolean;
            maxLocalRetries: number;
            maxProviderSwitches: number;
            providersHealthy: number;
            providersCircuitOpen: number;
        };
    };
};

export const EMPTY_CONTROL_PLANE_PAYLOAD: ControlPlanePayload = {
    config: {
        version: 2,
        roles: {},
        governance: {
            enabled: true,
            stickyRunModel: true,
            allowSameCapabilityFailover: true,
            strictCapabilityMatch: true,
            maxLocalRetries: 1,
            maxProviderSwitches: 2,
            defaultStreaming: true,
            providerHealthWindowDays: 7,
            providerFailureThreshold: 3,
            providerErrorRateThreshold: 0.6,
            budgets: {
                enabled: true,
                globalDailyCostLimit: 0,
                globalDailyTokenLimit: 0,
                runMaxCost: 0,
                runMaxTokens: 0,
                defaultProjectDailyCostLimit: 0,
                defaultProjectDailyTokenLimit: 0,
                projectBudgets: [],
            },
        },
        routingPolicies: {},
        providers: {},
    },
    summary: {
        providers: 0,
        enabledProviders: 0,
        models: 0,
        reasoningModels: 0,
        multimodalModels: 0,
        rolesAssigned: 0,
        capabilityClasses: {},
    },
    models: [],
    roles: [],
    modules: [],
    providersOverview: [],
    governanceSummary: {
        budgets: {
            enabled: true,
            today: {
                costTotal: 0,
                totalTokens: 0,
                invocations: 0,
            },
            limits: {
                globalDailyCostLimit: 0,
                globalDailyTokenLimit: 0,
                runMaxCost: 0,
                runMaxTokens: 0,
                defaultProjectDailyCostLimit: 0,
                defaultProjectDailyTokenLimit: 0,
            },
            projectOverrides: 0,
        },
        failover: {
            enabled: true,
            strictCapabilityMatch: true,
            maxLocalRetries: 1,
            maxProviderSwitches: 2,
            providersHealthy: 0,
            providersCircuitOpen: 0,
        },
    },
};
