import { type AIProvider, type AIModel } from "@admin/lib/model-hub/types";
import { type ProviderChannel, type PlatformLoginPreset, type LocalBackendPreset, inferPlatformLoginPreset, inferLocalBackendPreset } from "@admin/lib/models/provider-admin";
import { type ModelWireProtocol, editableProviderChannels } from "@admin/lib/model-hub/channels";
import { type ComfyWorkflowDraft, comfyWorkflowDraft } from "@admin/lib/model-hub/models";
import { type ControlPlaneModel } from "@admin/components/models/control-plane-types";
import { resolveMediaCapabilityModes } from "@admin/lib/models/media-capabilities";

export type ProviderDraft = {
    providerType: AIProvider['type']; providerCredentialMode: 'apiKey' | 'oauthFile';
    providerApiStandard: 'openai' | 'anthropic' | 'gemini' | 'comfyui';
    providerBaseUrl: string; providerChannels: ProviderChannel[]; providerDefaultChannelId: string;
    providerApiKey: string; providerOauthPath: string; platformLoginPreset: PlatformLoginPreset; localBackendPreset: LocalBackendPreset;
};
export type ModelDraft = {
    modelType: string; mediaCapabilityModes: string[]; modelProviderId: string; modelChannelId: string;
    modelWireProtocol: ModelWireProtocol; comfyWorkflow: ComfyWorkflowDraft; rerankApiFlavor: string;
};
export function createProviderDraft(provider: AIProvider | null): ProviderDraft {
    const channels = editableProviderChannels(provider, provider?.apiStandard || 'openai', provider?.baseUrl || '');
    return {
        providerType: provider?.type || 'API', providerCredentialMode: provider?.type === 'PLATFORM' ? 'oauthFile' : provider?.credentialMode || 'apiKey',
        providerApiStandard: (provider?.apiStandard as ProviderDraft['providerApiStandard']) || 'openai',
        providerBaseUrl: provider?.baseUrl || '', providerChannels: channels, providerDefaultChannelId: provider?.defaultChannelId || channels[0]?.id || 'default',
        providerApiKey: provider?.apiKey || '', providerOauthPath: provider?.oauthPath || '',
        platformLoginPreset: inferPlatformLoginPreset({ providerType: provider?.type, apiStandard: provider?.apiStandard, baseUrl: provider?.baseUrl, oauthPath: provider?.oauthPath, code: provider?.code, name: provider?.name }),
        localBackendPreset: inferLocalBackendPreset({ providerType: provider?.type, baseUrl: provider?.baseUrl, preset: provider?.localBackendPreset, code: provider?.code, name: provider?.name }),
    };
}
export function createModelDraft(model: AIModel | null, controlMeta: ControlPlaneModel | null, providers: AIProvider[]): ModelDraft {
    const provider = providers.find(item => item.id === model?.providerId) || providers[0];
    const channel = provider?.channels?.find(item => item.id === provider.defaultChannelId) || provider?.channels?.[0];
    const storedMediaLimits = model?.mediaLimits || {};
    const controlMediaLimits = controlMeta?.mediaLimits || {};
    const capabilityModes = Object.prototype.hasOwnProperty.call(storedMediaLimits, 'capabilityModes') ? storedMediaLimits.capabilityModes : controlMediaLimits.capabilityModes;
    const operationCapabilityProfiles = Object.prototype.hasOwnProperty.call(storedMediaLimits, 'operationCapabilityProfiles') ? storedMediaLimits.operationCapabilityProfiles : controlMediaLimits.operationCapabilityProfiles;
    const operationKinds = Array.isArray(storedMediaLimits.operationKinds) ? storedMediaLimits.operationKinds
        : Array.isArray(controlMediaLimits.operationKinds) ? controlMediaLimits.operationKinds : model?.operationKinds || [model?.endpointBinding?.operationKind];
    return {
        modelType: model?.type || 'TEXT',
        mediaCapabilityModes: resolveMediaCapabilityModes(model?.type || 'TEXT', capabilityModes, operationKinds, operationCapabilityProfiles),
        modelProviderId: model?.providerId || provider?.id || '',
        modelChannelId: model ? String(model.endpointBinding?.channelId || '') : channel?.id || '',
        modelWireProtocol: (model ? String(model.endpointBinding?.wireProtocol || '') : channel?.defaultWireProtocol || '') as ModelWireProtocol,
        rerankApiFlavor: model?.rerankApiFlavor || 'generic', comfyWorkflow: comfyWorkflowDraft(model?.mediaLimits),
    };
}
