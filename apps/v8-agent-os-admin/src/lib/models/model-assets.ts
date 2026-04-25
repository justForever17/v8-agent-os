const PROVIDER_LOGOS: Record<string, string> = {
    anthropic: "/model-assets/providers/anthropic.ico",
    deepseek: "/model-assets/providers/deepseek.ico",
    groq: "/model-assets/providers/groq.ico",
    huggingface: "/model-assets/providers/huggingface.ico",
    lmstudio: "/model-assets/providers/lmstudio.ico",
    mistral: "/model-assets/providers/mistral.ico",
    openrouter: "/model-assets/providers/openrouter.ico",
    siliconflow: "/model-assets/providers/siliconflow.png",
    xai: "/model-assets/providers/xai.ico",
};

const MODEL_ICONS: Array<{ pattern: RegExp; asset: string }> = [
    { pattern: /^claude/i, asset: "/model-assets/providers/anthropic.ico" },
    { pattern: /^deepseek/i, asset: "/model-assets/providers/deepseek.ico" },
    { pattern: /^mistral/i, asset: "/model-assets/providers/mistral.ico" },
];

function normalizeProviderId(value: string | undefined | null) {
    return String(value || "").trim().toLowerCase();
}

export function resolveProviderLogo(params: {
    providerId?: string | null;
    providerName?: string | null;
    explicitAsset?: string | null;
}) {
    if (params.explicitAsset) return params.explicitAsset;
    const providerId = normalizeProviderId(params.providerId);
    if (providerId && PROVIDER_LOGOS[providerId]) return PROVIDER_LOGOS[providerId];
    const providerName = normalizeProviderId(params.providerName);
    if (providerName) {
        const matchedKey = Object.keys(PROVIDER_LOGOS).find((key) => providerName.includes(key));
        if (matchedKey) return PROVIDER_LOGOS[matchedKey];
    }
    return null;
}

export function resolveModelIcon(params: {
    modelId?: string | null;
    providerId?: string | null;
    providerName?: string | null;
    explicitAsset?: string | null;
}) {
    if (params.explicitAsset) return params.explicitAsset;
    const modelId = String(params.modelId || "").trim();
    const modelMatch = MODEL_ICONS.find((item) => item.pattern.test(modelId));
    if (modelMatch) return modelMatch.asset;
    return resolveProviderLogo(params);
}
