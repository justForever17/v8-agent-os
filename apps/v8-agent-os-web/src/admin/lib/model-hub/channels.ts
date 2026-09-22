import { type ProviderChannel } from "@admin/lib/models/provider-admin";
import { type AIProvider } from "@admin/lib/model-hub/types";

export type ModelWireProtocol = "" | "openai.chat_completions" | "openai.responses" | "anthropic.messages" | "gemini.generate_content";

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
