import { LOBE_ICON_ASSETS } from "./lobe-icons.generated";

const LEGACY_PROVIDER_LOGOS: Record<string, string> = {
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

type LobeSlug = keyof typeof LOBE_ICON_ASSETS;

function normalizeText(value: string | undefined | null) {
    return String(value || "")
        .trim()
        .toLowerCase()
        .replace(/[_\s]+/g, "-");
}

function uniq(values: string[]) {
    return values.filter(Boolean).filter((value, index, list) => list.indexOf(value) === index);
}

function pickLobeIcon(slugs: string[]) {
    for (const slug of slugs) {
        const asset = LOBE_ICON_ASSETS[slug as LobeSlug];
        if (asset) return asset;
    }
    return null;
}

function providerSlugCandidates(providerId?: string | null, providerName?: string | null) {
    const id = normalizeText(providerId);
    const name = normalizeText(providerName);
    const haystack = `${id} ${name}`;
    const candidates: string[] = [];

    if (haystack.includes("gemini-cli") || haystack.includes("geminicli")) candidates.push("geminicli-color", "geminicli");
    if (haystack.includes("gemini") || haystack.includes("google")) candidates.push("gemini-color", "gemini", "google-color", "google");
    if (haystack.includes("openai") || haystack.includes("codex")) candidates.push("openai");
    if (haystack.includes("anthropic") || haystack.includes("claude")) candidates.push("anthropic", "claude-color", "claude");
    if (haystack.includes("deepseek")) candidates.push("deepseek-color", "deepseek");
    if (haystack.includes("openrouter")) candidates.push("openrouter");
    if (haystack.includes("siliconflow") || haystack.includes("silicon-cloud") || haystack.includes("siliconcloud")) candidates.push("siliconcloud-color", "siliconcloud");
    if (haystack.includes("huggingface") || haystack.includes("hugging-face")) candidates.push("huggingface-color", "huggingface");
    if (haystack.includes("mistral")) candidates.push("mistral-color", "mistral");
    if (haystack.includes("groq")) candidates.push("groq");
    if (haystack.includes("xai") || haystack.includes("grok")) candidates.push("xai", "grok");
    if (haystack.includes("qwen") || haystack.includes("dashscope") || haystack.includes("alibaba")) candidates.push("qwen-color", "qwen", "alibabacloud-color", "alibabacloud", "alibaba-color", "alibaba");
    if (haystack.includes("bailian") || haystack.includes("aliyun")) candidates.push("alibabacloud-color", "alibabacloud", "alibaba-color", "alibaba", "qwen-color", "qwen");
    if (haystack.includes("doubao")) candidates.push("doubao-color", "doubao");
    if (haystack.includes("volcengine") || haystack.includes("volcano")) candidates.push("volcengine-color", "volcengine");
    if (haystack.includes("moonshot")) candidates.push("moonshot");
    if (haystack.includes("kimi")) candidates.push("kimi-color", "kimi", "moonshot");
    if (haystack.includes("modelscope")) candidates.push("modelscope-color", "modelscope");
    if (haystack.includes("comfyui")) candidates.push("comfyui-color", "comfyui");
    if (haystack.includes("lmstudio")) candidates.push("lmstudio");
    if (haystack.includes("ollama")) candidates.push("ollama");
    if (haystack.includes("vllm")) candidates.push("vllm-color", "vllm");
    if (haystack.includes("minimax")) candidates.push("minimax-color", "minimax");
    if (haystack.includes("zhipu") || haystack.includes("bigmodel") || haystack.includes("glm")) candidates.push("zhipu-color", "zhipu", "chatglm-color", "chatglm");
    if (haystack.includes("hunyuan")) candidates.push("hunyuan-color", "hunyuan", "tencentcloud-color", "tencentcloud");
    if (haystack.includes("tencent")) candidates.push("tencentcloud-color", "tencentcloud");
    if (haystack.includes("cohere")) candidates.push("cohere-color", "cohere");
    if (haystack.includes("voyage")) candidates.push("voyage-color", "voyage");
    if (haystack.includes("jina")) candidates.push("jina");
    if (haystack.includes("baai") || haystack.includes("bge")) candidates.push("baai");
    if (haystack.includes("meta") || haystack.includes("llama")) candidates.push("meta-color", "meta");

    candidates.push(id, name);
    return uniq(candidates);
}

function modelSlugCandidates(modelId?: string | null) {
    const id = normalizeText(modelId);
    const candidates: string[] = [];

    if (/^(gpt|o1|o3|o4|codex)/.test(id) || id.includes("openai")) candidates.push("openai");
    if (id.includes("claude")) candidates.push("claude-color", "claude", "anthropic");
    if (id.includes("gemini")) candidates.push("gemini-color", "gemini");
    if (id.includes("deepseek")) candidates.push("deepseek-color", "deepseek");
    if (id.includes("qwen") || id.includes("qwq")) candidates.push("qwen-color", "qwen");
    if (id.includes("doubao") || id.includes("seedance")) candidates.push("doubao-color", "doubao", "volcengine-color", "volcengine");
    if (id.includes("kimi")) candidates.push("kimi-color", "kimi", "moonshot");
    if (id.includes("moonshot")) candidates.push("moonshot", "kimi-color", "kimi");
    if (id.includes("grok")) candidates.push("grok", "xai");
    if (id.includes("mistral") || id.includes("mixtral") || id.includes("codestral")) candidates.push("mistral-color", "mistral");
    if (id.includes("llama") || id.includes("meta")) candidates.push("meta-color", "meta");
    if (id.includes("bge") || id.includes("baai")) candidates.push("baai");
    if (id.includes("cohere") || id.includes("command") || id.includes("rerank")) candidates.push("cohere-color", "cohere");
    if (id.includes("voyage")) candidates.push("voyage-color", "voyage");
    if (id.includes("jina")) candidates.push("jina");
    if (id.includes("glm") || id.includes("zhipu")) candidates.push("zhipu-color", "zhipu", "chatglm-color", "chatglm");
    if (id.includes("hunyuan")) candidates.push("hunyuan-color", "hunyuan");
    if (id.includes("minimax")) candidates.push("minimax-color", "minimax");
    if (id.includes("comfyui")) candidates.push("comfyui-color", "comfyui");
    if (id.includes("ollama")) candidates.push("ollama");
    if (id.includes("lmstudio")) candidates.push("lmstudio");
    if (id.includes("vllm")) candidates.push("vllm-color", "vllm");

    candidates.push(id);
    return uniq(candidates);
}

export function resolveProviderLogo(params: {
    providerId?: string | null;
    providerName?: string | null;
    explicitAsset?: string | null;
}) {
    if (params.explicitAsset) return params.explicitAsset;
    const lobeAsset = pickLobeIcon(providerSlugCandidates(params.providerId, params.providerName));
    if (lobeAsset) return lobeAsset;

    const providerId = normalizeText(params.providerId);
    if (providerId && LEGACY_PROVIDER_LOGOS[providerId]) return LEGACY_PROVIDER_LOGOS[providerId];
    const providerName = normalizeText(params.providerName);
    if (providerName) {
        const matchedKey = Object.keys(LEGACY_PROVIDER_LOGOS).find((key) => providerName.includes(key));
        if (matchedKey) return LEGACY_PROVIDER_LOGOS[matchedKey];
    }
    return null;
}

export function resolveModelIcon(params: {
    modelId?: string | null;
    providerId?: string | null;
    providerName?: string | null;
    explicitAsset?: string | null;
}) {
    const modelAsset = pickLobeIcon(modelSlugCandidates(params.modelId));
    if (modelAsset) return modelAsset;
    return resolveProviderLogo(params);
}
