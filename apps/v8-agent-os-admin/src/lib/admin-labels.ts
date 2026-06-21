import type { TranslationKey } from "@/lib/locale";
import type { TranslateFn } from "@/i18n/admin-legacy";

type LabelOption = {
    value: string;
    labelKey: TranslationKey;
    descriptionKey?: TranslationKey;
};

type LabelDomain =
    | "providerType"
    | "providerCredentialMode"
    | "providerApiStandard"
    | "modelType"
    | "rerankApiFlavor"
    | "localBackendPreset"
    | "platformLoginPreset"
    | "toolMode"
    | "toolExposurePolicy"
    | "networkEnrollmentMode"
    | "networkScopeMode"
    | "networkPeerSource"
    | "dependencyRequiredness"
    | "workerType"
    | "workerCwdPolicy"
    | "workerSessionMode";

const LABEL_DOMAINS: Record<LabelDomain, LabelOption[]> = {
    providerType: [
        { value: "API", labelKey: "admin.enums.providerType.api" },
        { value: "LOCAL", labelKey: "admin.enums.providerType.local" },
        { value: "PLATFORM", labelKey: "admin.enums.providerType.platform" },
    ],
    providerCredentialMode: [
        { value: "apiKey", labelKey: "admin.enums.providerCredentialMode.apiKey" },
        { value: "oauthFile", labelKey: "admin.enums.providerCredentialMode.oauthFile" },
    ],
    providerApiStandard: [
        { value: "openai", labelKey: "admin.enums.providerApiStandard.openai" },
        { value: "anthropic", labelKey: "admin.enums.providerApiStandard.anthropic" },
        { value: "gemini", labelKey: "admin.enums.providerApiStandard.gemini" },
        { value: "comfyui", labelKey: "admin.enums.providerApiStandard.comfyui" },
    ],
    modelType: [
        { value: "TEXT", labelKey: "admin.enums.modelType.text" },
        { value: "CHAT", labelKey: "admin.enums.modelType.chat" },
        { value: "LLM", labelKey: "admin.enums.modelType.llm" },
        { value: "MULTIMODAL", labelKey: "admin.enums.modelType.multimodal" },
        { value: "MEDIA", labelKey: "admin.enums.modelType.media" },
        { value: "IMAGE", labelKey: "admin.enums.modelType.image" },
        { value: "VIDEO", labelKey: "admin.enums.modelType.video" },
        { value: "AUDIO", labelKey: "admin.enums.modelType.audio" },
        { value: "VOICE", labelKey: "admin.enums.modelType.voice" },
        { value: "MUSIC", labelKey: "admin.enums.modelType.music" },
        { value: "WORKFLOW", labelKey: "admin.enums.modelType.workflow" },
        { value: "MODEL3D", labelKey: "admin.enums.modelType.model3d" },
        { value: "EMBEDDING", labelKey: "admin.enums.modelType.embedding" },
        { value: "RERANK", labelKey: "admin.enums.modelType.rerank" },
        { value: "RERANKER", labelKey: "admin.enums.modelType.rerank" },
    ],
    rerankApiFlavor: [
        { value: "generic", labelKey: "admin.enums.rerankApiFlavor.generic" },
    ],
    localBackendPreset: [
        { value: "ollama", labelKey: "admin.enums.localBackendPreset.ollama" },
        { value: "nexa", labelKey: "admin.enums.localBackendPreset.nexa" },
        { value: "vllm", labelKey: "admin.enums.localBackendPreset.vllm" },
        { value: "lmstudio", labelKey: "admin.enums.localBackendPreset.lmstudio" },
    ],
    platformLoginPreset: [
        { value: "codex", labelKey: "admin.enums.platformLoginPreset.codex" },
    ],
    toolMode: [
        { value: "contextual_auto", labelKey: "admin.enums.toolMode.contextualAuto" },
        { value: "explicit", labelKey: "admin.enums.toolMode.explicit" },
    ],
    toolExposurePolicy: [
        { value: "contextual_auto", labelKey: "admin.enums.toolExposurePolicy.contextualAuto" },
        { value: "explicit_only", labelKey: "admin.enums.toolExposurePolicy.explicitOnly" },
        { value: "none", labelKey: "admin.enums.toolExposurePolicy.none" },
        { value: "task_brief_driven", labelKey: "admin.enums.toolExposurePolicy.taskBriefDriven" },
    ],
    networkEnrollmentMode: [
        { value: "manual", labelKey: "admin.enums.networkEnrollmentMode.manual" },
        { value: "open", labelKey: "admin.enums.networkEnrollmentMode.open" },
    ],
    networkScopeMode: [
        { value: "explicit", labelKey: "admin.enums.networkScopeMode.explicit" },
        { value: "open", labelKey: "admin.enums.networkScopeMode.open" },
    ],
    networkPeerSource: [
        { value: "lan", labelKey: "admin.enums.networkPeerSource.lan" },
        { value: "bootstrap", labelKey: "admin.enums.networkPeerSource.bootstrap" },
        { value: "manual", labelKey: "admin.enums.networkPeerSource.manual" },
        { value: "trusted", labelKey: "admin.enums.networkPeerSource.trusted" },
        { value: "discovered", labelKey: "admin.enums.networkPeerSource.discovered" },
    ],
    dependencyRequiredness: [
        { value: "required", labelKey: "admin.enums.dependencyRequiredness.required" },
        { value: "conditional", labelKey: "admin.enums.dependencyRequiredness.conditional" },
        { value: "optional", labelKey: "admin.enums.dependencyRequiredness.optional" },
    ],
    workerType: [
        { value: "custom", labelKey: "admin.enums.workerType.custom" },
        { value: "claude_code", labelKey: "admin.enums.workerType.claudeCode" },
    ],
    workerCwdPolicy: [
        { value: "inherit_workspace", labelKey: "admin.enums.workerCwdPolicy.inheritWorkspace" },
        { value: "runtime_temp", labelKey: "admin.enums.workerCwdPolicy.runtimeTemp" },
        { value: "explicit", labelKey: "admin.enums.workerCwdPolicy.explicit" },
    ],
    workerSessionMode: [
        { value: "interactive", labelKey: "admin.enums.workerSessionMode.interactive" },
        { value: "oneshot", labelKey: "admin.enums.workerSessionMode.oneshot" },
        { value: "print", labelKey: "admin.enums.workerSessionMode.print" },
    ],
};

function normalizeAdminValue(value: string) {
    return String(value || "").trim().toLowerCase();
}

function humanizeAdminValue(value: string) {
    const source = String(value || "").trim();
    if (!source) {
        return "";
    }
    const collapsed = source.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    if (!collapsed) {
        return source;
    }
    return collapsed.charAt(0).toUpperCase() + collapsed.slice(1);
}

export function getAdminOptions(domain: LabelDomain) {
    return LABEL_DOMAINS[domain];
}

export function resolveAdminLabel(
    t: TranslateFn,
    domain: LabelDomain,
    value: string | null | undefined,
    options?: {
        fallbackKey?: TranslationKey;
        showRawWhenUnknown?: boolean;
    },
) {
    const raw = String(value || "").trim();
    const normalized = normalizeAdminValue(raw);
    const matched = LABEL_DOMAINS[domain].find((item) => normalizeAdminValue(item.value) === normalized);
    if (matched) {
        return t(matched.labelKey);
    }
    if (!raw) {
        return options?.fallbackKey ? t(options.fallbackKey) : "";
    }
    const humanized = humanizeAdminValue(raw);
    return options?.showRawWhenUnknown === false || humanized === raw
        ? humanized
        : `${humanized} (${raw})`;
}
