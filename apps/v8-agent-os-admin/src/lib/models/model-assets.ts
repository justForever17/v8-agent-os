import { LOBE_ICON_ASSETS } from "./lobe-icons.generated";

const LEGACY_PROVIDER_LOGOS: Record<string, string> = {
    anthropic: "/model-assets/providers/anthropic.ico",
    deepseek: "/model-assets/providers/deepseek.ico",
    groq: "/model-assets/providers/groq.ico",
    huggingface: "/model-assets/providers/huggingface.ico",
    hitem3d: "/model-assets/providers/hitem3d.svg",
    hyper3d: "/model-assets/providers/hyper3d.svg",
    lmstudio: "/model-assets/providers/lmstudio.ico",
    mistral: "/model-assets/providers/mistral.ico",
    mureka: "/model-assets/providers/mureka.svg",
    openrouter: "/model-assets/providers/openrouter.ico",
    siliconflow: "/model-assets/providers/siliconflow.png",
    xai: "/model-assets/providers/xai.ico",
    "xiaomi-mimo": "/model-assets/providers/xiaomi-mimo.svg",
    "xiaomi-mimo-tokenplan": "/model-assets/providers/xiaomi-mimo.svg",
    "xiaomi-mimo-tokenplan-anthropic": "/model-assets/providers/xiaomi-mimo.svg",
    xiaomimimo: "/model-assets/providers/xiaomi-mimo.svg",
    tokenplan: "/model-assets/providers/xiaomi-mimo.svg",
    mimo: "/model-assets/providers/xiaomi-mimo.svg",
    happyhorse: "/model-assets/providers/happyhorse.svg",
    "v8-audio": "/model-assets/providers/v8-audio.svg",
    "fish-audio": "/model-assets/providers/fish-audio.svg",
    "black-forest-labs": "/model-assets/providers/black-forest-labs.svg",
    perplexity: "/model-assets/providers/perplexity.svg",
    fireworks: "/model-assets/providers/fireworks.svg",
    cerebras: "/model-assets/providers/cerebras.png",
    "nvidia-nim": "/model-assets/providers/nvidia-nim.svg",
    ai21: "/model-assets/providers/ai21.png",
    "baidu-qianfan": "/model-assets/providers/baidu-qianfan.svg",
    stepfun: "/model-assets/providers/stepfun.png",
    baichuan: "/model-assets/providers/baichuan.png",
    together: "/model-assets/providers/together.png",
    nexa: "/model-assets/providers/nexa.png",
    ideogram: "/model-assets/providers/ideogram.png",
    leonardo: "/model-assets/providers/leonardo.png",
    vidu: "/model-assets/providers/vidu.svg",
    pika: "/model-assets/providers/pika.ico",
    haiper: "/model-assets/providers/haiper.svg",
    heygen: "/model-assets/providers/heygen.ico",
    synthesia: "/model-assets/providers/synthesia.png",
    "d-id": "/model-assets/providers/d-id.png",
    tavus: "/model-assets/providers/tavus.ico",
    hedra: "/model-assets/providers/hedra.svg",
    shotstack: "/model-assets/providers/shotstack.ico",
    creatomate: "/model-assets/providers/creatomate.ico",
    cartesia: "/model-assets/providers/cartesia.png",
    playht: "/model-assets/providers/playht.png",
    "azure-speech": "/model-assets/providers/azure-speech.ico",
    "amazon-polly": "/model-assets/providers/amazon-polly.ico",
    udio: "/model-assets/providers/udio.ico",
    meshy: "/model-assets/providers/meshy.svg",
    csm: "/model-assets/providers/csm.svg",
    "3d-ai-studio": "/model-assets/providers/3d-ai-studio.ico",
};

const LOCAL_MODEL_LOGOS: Record<string, string> = {
    hitem3d: "/model-assets/providers/hitem3d.svg",
    hyper3d: "/model-assets/providers/hyper3d.svg",
    mureka: "/model-assets/providers/mureka.svg",
    mimo: "/model-assets/providers/xiaomi-mimo.svg",
    happyhorse: "/model-assets/providers/happyhorse.svg",
    "v8-audio": "/model-assets/providers/v8-audio.svg",
    fish: "/model-assets/providers/fish-audio.svg",
    flux: "/model-assets/providers/black-forest-labs.svg",
    sonar: "/model-assets/providers/perplexity.svg",
    jamba: "/model-assets/providers/ai21.png",
    ernie: "/model-assets/providers/baidu-qianfan.svg",
    "baichuan": "/model-assets/providers/baichuan.png",
    ideogram: "/model-assets/providers/ideogram.png",
    leonardo: "/model-assets/providers/leonardo.png",
    vidu: "/model-assets/providers/vidu.svg",
    pika: "/model-assets/providers/pika.ico",
    haiper: "/model-assets/providers/haiper.svg",
    heygen: "/model-assets/providers/heygen.ico",
    synthesia: "/model-assets/providers/synthesia.png",
    tavus: "/model-assets/providers/tavus.ico",
    hedra: "/model-assets/providers/hedra.svg",
    shotstack: "/model-assets/providers/shotstack.ico",
    creatomate: "/model-assets/providers/creatomate.ico",
    cartesia: "/model-assets/providers/cartesia.png",
    playht: "/model-assets/providers/playht.png",
    polly: "/model-assets/providers/amazon-polly.ico",
    udio: "/model-assets/providers/udio.ico",
    meshy: "/model-assets/providers/meshy.svg",
    csm: "/model-assets/providers/csm.svg",
    "3d-ai-studio": "/model-assets/providers/3d-ai-studio.ico",
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

function pickLocalModelIcon(modelId?: string | null) {
    const id = normalizeText(modelId);
    for (const [key, asset] of Object.entries(LOCAL_MODEL_LOGOS)) {
        if (id.includes(key) || id.includes(key.replace("3d", "-3d"))) return asset;
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
    if (haystack.includes("xiaomi") || haystack.includes("mimo") || haystack.includes("tokenplan")) candidates.push("mimo", "xiaomi-mimo", "tokenplan");
    if (haystack.includes("moonshot")) candidates.push("moonshot");
    if (haystack.includes("kimi")) candidates.push("kimi-color", "kimi", "moonshot");
    if (haystack.includes("modelscope")) candidates.push("modelscope-color", "modelscope");
    if (haystack.includes("comfyui")) candidates.push("comfyui-color", "comfyui");
    if (haystack.includes("fal")) candidates.push("fal-color", "fal");
    if (haystack.includes("suno")) candidates.push("suno");
    if (haystack.includes("elevenlabs") || haystack.includes("eleven-labs")) candidates.push("elevenlabs");
    if (haystack.includes("stability")) candidates.push("stability-color", "stability");
    if (haystack.includes("replicate")) candidates.push("replicate");
    if (haystack.includes("tripo")) candidates.push("tripo-color", "tripo");
    if (haystack.includes("runway")) candidates.push("runway");
    if (haystack.includes("luma")) candidates.push("luma-color", "luma");
    if (haystack.includes("kling")) candidates.push("kling-color", "kling");
    if (haystack.includes("happyhorse") || haystack.includes("happy-horse")) candidates.push("happyhorse");
    if (haystack.includes("v8-audio") || haystack.includes("v8 audio")) candidates.push("v8-audio");
    if (haystack.includes("fish-audio") || haystack.includes("fish audio")) candidates.push("fish-audio");
    if (haystack.includes("black-forest") || haystack.includes("black forest") || haystack.includes("flux")) candidates.push("black-forest-labs");
    if (haystack.includes("meshy")) candidates.push("meshy");
    if (haystack.includes("csm") || haystack.includes("common-sense-machines") || haystack.includes("common sense machines")) candidates.push("csm");
    if (haystack.includes("3d-ai-studio") || haystack.includes("3d ai studio")) candidates.push("3d-ai-studio");
    if (haystack.includes("mureka")) candidates.push("mureka");
    if (haystack.includes("hitem3d") || haystack.includes("hitem-3d")) candidates.push("hitem3d");
    if (haystack.includes("hyper3d") || haystack.includes("hyper-3d")) candidates.push("hyper3d");
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
    if (haystack.includes("together")) candidates.push("together");
    if (haystack.includes("nexa")) candidates.push("nexa");
    if (haystack.includes("perplexity") || haystack.includes("sonar")) candidates.push("perplexity");
    if (haystack.includes("fireworks")) candidates.push("fireworks");
    if (haystack.includes("cerebras")) candidates.push("cerebras");
    if (haystack.includes("nvidia") || haystack.includes("nim")) candidates.push("nvidia-nim");
    if (haystack.includes("ai21") || haystack.includes("jamba")) candidates.push("ai21");
    if (haystack.includes("baidu") || haystack.includes("qianfan") || haystack.includes("ernie")) candidates.push("baidu-qianfan");
    if (haystack.includes("stepfun") || haystack.includes("step-")) candidates.push("stepfun");
    if (haystack.includes("baichuan")) candidates.push("baichuan");
    if (haystack.includes("ideogram")) candidates.push("ideogram");
    if (haystack.includes("leonardo")) candidates.push("leonardo");
    if (haystack.includes("vidu")) candidates.push("vidu");
    if (haystack.includes("pika")) candidates.push("pika");
    if (haystack.includes("haiper")) candidates.push("haiper");
    if (haystack.includes("heygen")) candidates.push("heygen");
    if (haystack.includes("synthesia")) candidates.push("synthesia");
    if (haystack.includes("d-id") || haystack.includes("d id")) candidates.push("d-id");
    if (haystack.includes("tavus")) candidates.push("tavus");
    if (haystack.includes("hedra")) candidates.push("hedra");
    if (haystack.includes("shotstack")) candidates.push("shotstack");
    if (haystack.includes("creatomate")) candidates.push("creatomate");
    if (haystack.includes("cartesia")) candidates.push("cartesia");
    if (haystack.includes("playht") || haystack.includes("play-ht")) candidates.push("playht");
    if (haystack.includes("azure") && haystack.includes("speech")) candidates.push("azure-speech");
    if (haystack.includes("amazon") || haystack.includes("polly")) candidates.push("amazon-polly");
    if (haystack.includes("udio")) candidates.push("udio");

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
    if (id.includes("doubao") || id.includes("seedance") || id.includes("seedream") || id.includes("seed3d")) candidates.push("doubao-color", "doubao", "volcengine-color", "volcengine");
    if (id.includes("mimo")) candidates.push("mimo", "xiaomi-mimo");
    if (id.includes("sora")) candidates.push("openai");
    if (id.includes("veo")) candidates.push("gemini-color", "gemini", "google-color", "google");
    if (id.includes("kimi")) candidates.push("kimi-color", "kimi", "moonshot");
    if (id.includes("moonshot")) candidates.push("moonshot", "kimi-color", "kimi");
    if (id.includes("grok")) candidates.push("grok", "xai");
    if (id.includes("mistral") || id.includes("mixtral") || id.includes("codestral")) candidates.push("mistral-color", "mistral");
    if (id.includes("llama") || id.includes("meta")) candidates.push("meta-color", "meta");
    if (id.includes("bge") || id.includes("baai")) candidates.push("baai");
    if (id.includes("cohere") || id.includes("command") || id.includes("rerank")) candidates.push("cohere-color", "cohere");
    if (id.includes("voyage")) candidates.push("voyage-color", "voyage");
    if (id.includes("jina")) candidates.push("jina");
    if (id.includes("glm") || id.includes("zhipu")) candidates.push("glmv-color", "glmv", "zhipu-color", "zhipu", "chatglm-color", "chatglm");
    if (id.includes("cogview")) candidates.push("cogview-color", "cogview", "zhipu-color", "zhipu");
    if (id.includes("cogvideo")) candidates.push("cogvideo-color", "cogvideo", "zhipu-color", "zhipu");
    if (id.includes("hunyuan")) candidates.push("hunyuan-color", "hunyuan");
    if (id.includes("minimax")) candidates.push("minimax-color", "minimax");
    if (id.includes("fal")) candidates.push("fal-color", "fal");
    if (id.includes("suno")) candidates.push("suno");
    if (id.includes("eleven")) candidates.push("elevenlabs");
    if (id.includes("stability") || id.includes("stable-image")) candidates.push("stability-color", "stability");
    if (id.includes("replicate")) candidates.push("replicate");
    if (id.includes("tripo")) candidates.push("tripo-color", "tripo");
    if (id.includes("runway") || id.includes("gen4")) candidates.push("runway");
    if (id.includes("luma") || id.includes("ray-")) candidates.push("luma-color", "luma");
    if (id.includes("kling")) candidates.push("kling-color", "kling");
    if (id.includes("happyhorse") || id.includes("happy-horse")) candidates.push("happyhorse");
    if (id.includes("v8-audio")) candidates.push("v8-audio");
    if (id.includes("fish-speech") || id.includes("fish-audio")) candidates.push("fish-audio");
    if (id.includes("flux")) candidates.push("black-forest-labs");
    if (id.includes("meshy")) candidates.push("meshy");
    if (id.includes("csm")) candidates.push("csm");
    if (id.includes("trellis") || id.includes("3d-ai-studio")) candidates.push("3d-ai-studio");
    if (id.includes("mureka")) candidates.push("mureka");
    if (id.includes("hitem3d") || id.includes("hitem-3d")) candidates.push("hitem3d");
    if (id.includes("hyper3d") || id.includes("hyper-3d")) candidates.push("hyper3d");
    if (id.includes("comfyui")) candidates.push("comfyui-color", "comfyui");
    if (id.includes("ollama")) candidates.push("ollama");
    if (id.includes("lmstudio")) candidates.push("lmstudio");
    if (id.includes("vllm")) candidates.push("vllm-color", "vllm");
    if (id.includes("sonar") || id.includes("perplexity")) candidates.push("perplexity");
    if (id.includes("fireworks")) candidates.push("fireworks");
    if (id.includes("cerebras")) candidates.push("cerebras");
    if (id.includes("nvidia") || id.includes("nim")) candidates.push("nvidia-nim");
    if (id.includes("jamba") || id.includes("ai21")) candidates.push("ai21");
    if (id.includes("ernie") || id.includes("qianfan")) candidates.push("baidu-qianfan");
    if (id.includes("step-") || id.includes("stepfun")) candidates.push("stepfun");
    if (id.includes("baichuan")) candidates.push("baichuan");
    if (id.includes("ideogram")) candidates.push("ideogram");
    if (id.includes("leonardo")) candidates.push("leonardo");
    if (id.includes("vidu")) candidates.push("vidu");
    if (id.includes("pika")) candidates.push("pika");
    if (id.includes("haiper")) candidates.push("haiper");
    if (id.includes("heygen")) candidates.push("heygen");
    if (id.includes("synthesia")) candidates.push("synthesia");
    if (id.includes("d-id")) candidates.push("d-id");
    if (id.includes("tavus")) candidates.push("tavus");
    if (id.includes("hedra")) candidates.push("hedra");
    if (id.includes("shotstack")) candidates.push("shotstack");
    if (id.includes("creatomate")) candidates.push("creatomate");
    if (id.includes("cartesia") || id.includes("sonic")) candidates.push("cartesia");
    if (id.includes("playht") || id.includes("play-ht")) candidates.push("playht");
    if (id.includes("azure") && id.includes("tts")) candidates.push("azure-speech");
    if (id.includes("polly")) candidates.push("amazon-polly");
    if (id.includes("udio")) candidates.push("udio");

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
    if (params.explicitAsset) return params.explicitAsset;
    const localAsset = pickLocalModelIcon(params.modelId);
    if (localAsset) return localAsset;
    const modelAsset = pickLobeIcon(modelSlugCandidates(params.modelId));
    if (modelAsset) return modelAsset;
    return resolveProviderLogo(params);
}
