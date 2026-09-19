import { type LocalBackendPreset, type ProviderChannel } from "@/lib/models/provider-admin";
import { type ControlPlaneModel, type ProviderOverview } from "@/components/models/control-plane-types";
import { type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { type CatalogProvider } from "@/lib/model-hub/catalog";

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

export type ModelHubBootstrapPayload = {
    providers?: AIProvider[];
    models?: AIModel[];
    hubEnvelope?: ConfigRegistryEnvelope<ModelHubPayload> | null;
    defaultModel?: { modelRef?: string | null; modelId?: string | null; value?: string | null };
    catalog?: { providers?: CatalogProvider[] };
    audioConfig?: unknown;
};
