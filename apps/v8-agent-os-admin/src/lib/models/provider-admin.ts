export type ProviderCredentialMode = "apiKey" | "oauthFile";
export type PlatformLoginPreset = "qwenCode" | "geminiCli" | "codex";
export type ProviderApiStandard = "openai" | "anthropic" | "gemini";
export type LocalBackendPreset = "ollama" | "nexa" | "vllm" | "lmstudio";

export type PlatformLoginPresetConfig = {
    id: PlatformLoginPreset;
    label: string;
    description: string;
    apiStandard: ProviderApiStandard;
    baseUrl: string;
    oauthPath: string;
    supportState: "stable" | "preset-only";
    helpText: string;
};

export type LocalBackendPresetConfig = {
    id: LocalBackendPreset;
    label: string;
    description: string;
    apiStandard: ProviderApiStandard;
    baseUrl: string;
    apiKey: string;
    supportState: "stable" | "preset-only";
    helpText: string;
};

type EngineProviderMeta = {
    name?: string;
    description?: string;
    icon?: string | null;
    base_url?: string;
    api_key?: string;
    api_standard?: string;
    type?: string;
    is_enabled?: boolean;
    credential_mode?: string;
    oauth_preset?: string;
    oauth_ref?: string;
    local_backend_preset?: string;
};

type EngineProviderContainer = {
    provider?: EngineProviderMeta;
    models?: Record<string, Record<string, unknown>>;
};

const INVISIBLE_OAUTH_PATH_MARKERS = /[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g;

export const PLATFORM_LOGIN_PRESETS: Record<PlatformLoginPreset, PlatformLoginPresetConfig> = {
    qwenCode: {
        id: "qwenCode",
        label: "Qwen Code",
        description: "Qwen Portal OAuth 文件",
        apiStandard: "openai",
        baseUrl: "https://portal.qwen.ai/v1",
        oauthPath: "~/.qwen/oauth_creds.json",
        supportState: "stable",
        helpText: "正式支持。保存后会按 oauth: 文件引用读取 Qwen access_token。",
    },
    geminiCli: {
        id: "geminiCli",
        label: "Gemini CLI",
        description: "Gemini CLI OAuth 文件",
        apiStandard: "gemini",
        baseUrl: "https://cloudcode-pa.googleapis.com",
        oauthPath: "~/.gemini/oauth_creds.json",
        supportState: "stable",
        helpText: "支持 OAuth 文件接入与连接测试。保存后会按 Gemini CLI 预设读取本地 oauth_creds.json。",
    },
    codex: {
        id: "codex",
        label: "Codex",
        description: "OpenAI Codex auth.json",
        apiStandard: "openai",
        baseUrl: "https://chatgpt.com/backend-api",
        oauthPath: "~/.codex/auth.json",
        supportState: "stable",
        helpText: "正式支持。保存后会按 oauth: 文件引用读取 Codex access_token。",
    },
};

export const LOCAL_BACKEND_PRESETS: Record<LocalBackendPreset, LocalBackendPresetConfig> = {
    ollama: {
        id: "ollama",
        label: "Ollama",
        description: "本机 Ollama OpenAI 兼容入口",
        apiStandard: "openai",
        baseUrl: "http://127.0.0.1:11434/v1",
        apiKey: "ollama",
        supportState: "stable",
        helpText: "默认按 OpenAI 兼容模式连接 Ollama，本地聊天模型可直接测试。",
    },
    nexa: {
        id: "nexa",
        label: "Nexa",
        description: "本机 Nexa OpenAI 兼容入口",
        apiStandard: "openai",
        baseUrl: "http://127.0.0.1:18181/v1",
        apiKey: "",
        supportState: "preset-only",
        helpText: "当前按本地 OpenAI 兼容 HTTP provider 处理，保留 baseUrl / key 可编辑。",
    },
    vllm: {
        id: "vllm",
        label: "vLLM",
        description: "本机 vLLM OpenAI 兼容入口",
        apiStandard: "openai",
        baseUrl: "http://127.0.0.1:8000/v1",
        apiKey: "local-vllm",
        supportState: "stable",
        helpText: "适用于本机 vLLM 服务，文本模型和 rerank 都走本地 HTTP provider。",
    },
    lmstudio: {
        id: "lmstudio",
        label: "LM Studio",
        description: "本机 LM Studio OpenAI 兼容入口",
        apiStandard: "openai",
        baseUrl: "http://127.0.0.1:1234/v1",
        apiKey: "lm-studio",
        supportState: "stable",
        helpText: "保留现有 LM Studio 本地探针能力，并允许继续手改 baseUrl / key。",
    },
};

function sanitizeOauthPath(filepath: string): string {
    return String(filepath || "").replace(INVISIBLE_OAUTH_PATH_MARKERS, "").trim();
}

function maskPath(filepath: string): string {
    const trimmed = sanitizeOauthPath(filepath);
    if (!trimmed) return "";
    const normalized = trimmed.replace(/\\/g, "/");
    const parts = normalized.split("/").filter(Boolean);
    if (parts.length <= 2) {
        return trimmed;
    }
    return `…/${parts.slice(-2).join("/")}`;
}

export function inferCredentialMode(rawCredential: string, providerType?: string): ProviderCredentialMode {
    if ((rawCredential || "").startsWith("oauth:")) {
        return "oauthFile";
    }
    if ((providerType || "").toUpperCase() === "PLATFORM") {
        return "oauthFile";
    }
    return "apiKey";
}

export function inferPlatformLoginPreset(params: {
    providerType?: string;
    apiStandard?: string;
    baseUrl?: string | null;
    oauthPath?: string | null;
    code?: string | null;
    name?: string | null;
}): PlatformLoginPreset {
    const normalizedType = String(params.providerType || "").toUpperCase();
    const normalizedBaseUrl = String(params.baseUrl || "").trim().toLowerCase();
    const normalizedOauthPath = sanitizeOauthPath(String(params.oauthPath || "")).replace(/\\/g, "/").toLowerCase();
    const normalizedCode = String(params.code || "").trim().toLowerCase();
    const normalizedName = String(params.name || "").trim().toLowerCase();
    const apiStandard = String(params.apiStandard || "openai").toLowerCase();

    if (normalizedType !== "PLATFORM") {
        return "qwenCode";
    }
    if (
        normalizedBaseUrl.includes("portal.qwen.ai") ||
        normalizedOauthPath.includes("/.qwen/oauth_creds.json") ||
        normalizedCode.includes("qwen") ||
        normalizedName.includes("qwen")
    ) {
        return "qwenCode";
    }
    if (
        normalizedBaseUrl.includes("chatgpt.com/backend-api") ||
        normalizedOauthPath.includes("/.codex/auth.json") ||
        normalizedCode.includes("codex") ||
        normalizedName.includes("codex")
    ) {
        return "codex";
    }
    if (
        normalizedBaseUrl.includes("cloudcode-pa.googleapis.com") ||
        normalizedOauthPath.includes("/.gemini/oauth_creds.json") ||
        normalizedCode.includes("gemini") ||
        normalizedName.includes("gemini") ||
        apiStandard === "gemini"
    ) {
        return "geminiCli";
    }
    return "qwenCode";
}

export function getPlatformLoginPresetConfig(preset: PlatformLoginPreset): PlatformLoginPresetConfig {
    return PLATFORM_LOGIN_PRESETS[preset];
}

export function inferLocalBackendPreset(params: {
    providerType?: string;
    baseUrl?: string | null;
    preset?: string | null;
    code?: string | null;
    name?: string | null;
}): LocalBackendPreset {
    const normalizedType = String(params.providerType || "").toUpperCase();
    if (normalizedType !== "LOCAL") {
        return "ollama";
    }
    const explicitPreset = String(params.preset || "").trim().toLowerCase();
    if (explicitPreset === "ollama" || explicitPreset === "nexa" || explicitPreset === "vllm" || explicitPreset === "lmstudio") {
        return explicitPreset;
    }
    const normalizedBaseUrl = String(params.baseUrl || "").trim().toLowerCase();
    const normalizedCode = String(params.code || "").trim().toLowerCase();
    const normalizedName = String(params.name || "").trim().toLowerCase();
    const fingerprint = `${normalizedBaseUrl} ${normalizedCode} ${normalizedName}`;
    if (fingerprint.includes("11434") || fingerprint.includes("ollama")) {
        return "ollama";
    }
    if (fingerprint.includes("1234") || fingerprint.includes("lmstudio") || fingerprint.includes("lm studio")) {
        return "lmstudio";
    }
    if (fingerprint.includes("8000") || fingerprint.includes("vllm")) {
        return "vllm";
    }
    if (fingerprint.includes("18181") || fingerprint.includes("nexa")) {
        return "nexa";
    }
    return "ollama";
}

export function getLocalBackendPresetConfig(preset: LocalBackendPreset): LocalBackendPresetConfig {
    return LOCAL_BACKEND_PRESETS[preset];
}

export function mapEngineProvider(providerId: string, providerData: EngineProviderContainer) {
    const meta = providerData.provider || {};
    const rawCredential = String(meta.api_key || "");
    const credentialMode = (String(meta.credential_mode || "").trim() as ProviderCredentialMode) || inferCredentialMode(rawCredential, meta.type);
    const oauthPath = rawCredential.startsWith("oauth:") ? sanitizeOauthPath(rawCredential.slice(6)) : "";
    const oauthRef = String(meta.oauth_ref || "").trim();
    const platformLoginPreset = inferPlatformLoginPreset({
        providerType: meta.type,
        apiStandard: meta.api_standard,
        baseUrl: meta.base_url,
        oauthPath,
        code: providerId,
        name: meta.name,
    });
    const localBackendPreset = inferLocalBackendPreset({
        providerType: meta.type,
        baseUrl: meta.base_url,
        preset: meta.local_backend_preset,
        code: providerId,
        name: meta.name,
    });
    const models = Object.entries(providerData.models || {}).map(([modelKey, modelMetaRaw]) => {
        const modelMeta = (modelMetaRaw && typeof modelMetaRaw === "object")
            ? (modelMetaRaw as Record<string, unknown>)
            : {};
        return {
            id: modelKey,
            providerId: providerId,
            name: String(modelMeta.name || modelKey),
            modelId: modelKey,
            type: String(modelMeta.type || "TEXT"),
            contextWindow: typeof modelMeta.contextWindow === "number" ? modelMeta.contextWindow : null,
            maxTokens: typeof modelMeta.maxTokens === "number" ? modelMeta.maxTokens : null,
            rerankApiFlavor: String(modelMeta.rerank_api_flavor || modelMeta.rerankApiFlavor || ""),
            isEnabled: modelMeta.isEnabled !== false,
        };
    });

    return {
        id: providerId,
        name: meta.name || providerId,
        code: providerId,
        description: meta.description || "",
        icon: meta.icon || "",
        baseUrl: meta.base_url || "",
        apiKey: credentialMode === "apiKey" && rawCredential ? "****" : "",
        type: meta.type || "API",
        apiStandard: meta.api_standard || "openai",
        isEnabled: meta.is_enabled !== false,
        credentialMode,
        hasCredential: Boolean(rawCredential),
        oauthPath,
        oauthRef,
        oauthPathMasked: oauthPath ? maskPath(oauthPath) : "",
        platformLoginPreset,
        localBackendPreset,
        models,
    };
}

export function buildStoredCredential(params: {
    providerType?: string;
    credentialMode?: string;
    apiKey?: string;
    oauthPath?: string;
    existingRawCredential?: string;
}): string {
    const existingRawValue = String(params.existingRawCredential || "");
    const existingRaw = existingRawValue.startsWith("oauth:")
        ? `oauth:${sanitizeOauthPath(existingRawValue.slice(6))}`
        : existingRawValue.trim();
    const nextMode = inferCredentialMode(existingRaw, params.providerType) === "oauthFile" || params.credentialMode === "oauthFile"
        || (params.providerType || "").toUpperCase() === "PLATFORM"
        ? "oauthFile"
        : "apiKey";

    if (nextMode === "oauthFile") {
        const rawOauthPath = sanitizeOauthPath(String(params.oauthPath || ""));
        if (!rawOauthPath) {
            return existingRaw.startsWith("oauth:") ? existingRaw : "";
        }
        return rawOauthPath.startsWith("oauth:") ? rawOauthPath : `oauth:${rawOauthPath}`;
    }

    const rawApiKey = String(params.apiKey || "").trim();
    if (!rawApiKey || rawApiKey === "****") {
        return existingRaw;
    }
    return rawApiKey;
}
