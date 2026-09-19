import audioVoicePresets from "@/lib/models/audio-voice-presets.json";
import { type AIModel } from "@/lib/model-hub/types";
import { modelRefFor, normalizeModelType } from "@/lib/model-hub/models";
import { type ControlPlaneModel } from "@/components/models/control-plane-types";
import { extractErrorText } from "@/lib/model-hub/errors";

export type AudioRuntimeConfig = {
    stt: {
        active_provider: string;
        providers: {
            custom: {
                endpoint?: string;
                api_key?: string;
                protocol?: string;
                model?: string;
                language?: string;
                fileField?: string;
                responseTextPath?: string;
                headers?: string | Record<string, string>;
            };
            baidu: { app_id?: string; api_key?: string; secret_key?: string };
            volcengine?: { app_id?: string; access_token?: string; cluster?: string };
        };
        model_ref?: { modelRef?: string; mode?: string; language?: string; prompt?: string };
    };
    tts: {
        active_provider: string;
        edge_tts: { voice?: string; rate?: string; volume?: string };
        custom: {
            endpoint?: string;
            api_key?: string;
            voice?: string;
            protocol?: string;
            model?: string;
            format?: string;
            speed?: string;
            responseAudioPath?: string;
            headers?: string | Record<string, string>;
        };
        model_ref?: { modelRef?: string; voice?: string; format?: string; speed?: string };
    };
};

export type AudioVoicePreset = {
    value: string;
    label?: string;
    labelKey?: string;
};

export type AudioVoiceOption = {
    value: string;
    label: string;
    group?: string;
    deletable?: boolean;
    source?: "remote" | "preset" | "local_ledger" | string;
    availability?: "available" | "confirmed" | "pending_activation" | string;
};

export type TtsVoiceCapabilities = {
    clone?: boolean;
    design?: boolean;
    list?: boolean;
    delete?: boolean;
    preview?: boolean;
    commit?: boolean;
};

export type TtsVoiceAssetPolicy = {
    assetScope?: "durable_remote" | "provider_slot" | "ephemeral_request" | "qualification_only" | string;
    inventorySource?: "remote" | "local_projection" | "none" | string;
    designFlow?: "direct" | "ephemeral" | "preview_then_commit" | "qualification_only" | string;
    eligibilityStatus?: "available" | "eligible" | "requires_approval" | string;
    consentRequired?: boolean;
    docsUrl?: string;
    applicationUrl?: string;
};

export type TtsVoiceDesignCandidate = {
    generatedVoiceId: string;
    previewAudio: string;
};

export type TtsVoiceTextConstraint = {
    required?: boolean;
    minChars?: number;
    maxChars?: number | null;
};

export type TtsVoiceDesignConstraints = {
    prompt?: TtsVoiceTextConstraint;
    previewText?: TtsVoiceTextConstraint;
    voiceId?: TtsVoiceTextConstraint & {
        role?: "none" | "custom_id" | "prefix" | "provider_slot" | string;
        format?: string;
    };
};

export type TtsVoiceProviderInfo = {
    modelRef?: string;
    provider?: string;
    capabilities?: TtsVoiceCapabilities;
    assetPolicy?: TtsVoiceAssetPolicy;
    designConstraints?: TtsVoiceDesignConstraints;
    credentialStatus?: "configured" | "missing";
    sampleLimits?: {
        minDurationSeconds?: number;
        maxDurationSeconds?: number;
        maxBytes?: number;
        formats?: string[];
    };
};

export type AudioVoicePresetProvider = {
    match?: string[];
    protocol?: string;
    defaultEndpoint?: string;
    voices?: AudioVoicePreset[];
    supportsRemoteVoiceList?: boolean;
    remoteVoiceListPath?: string;
};

export type AudioVoicePresetTable = {
    providers?: Record<string, AudioVoicePresetProvider>;
};

export const TTS_MODEL_TYPES = new Set<string>(["AUDIO", "VOICE"]);

export const DEFAULT_AUDIO_CONFIG: AudioRuntimeConfig = {
    stt: {
        active_provider: "baidu",
        providers: {
            custom: { endpoint: "", api_key: "", protocol: "multipart", model: "", language: "zh-CN", fileField: "file", responseTextPath: "text", headers: "" },
            baidu: { app_id: "", api_key: "", secret_key: "" },
            volcengine: { app_id: "", access_token: "", cluster: "" },
        },
        model_ref: { modelRef: "", mode: "audio_input", language: "zh-CN", prompt: "" },
    },
    tts: {
        active_provider: "edge-tts",
        edge_tts: { voice: "zh-CN-XiaoxiaoNeural", rate: "+0%", volume: "+0%" },
        custom: { endpoint: "", api_key: "", voice: "", protocol: "json_audio_stream", model: "", format: "mp3", speed: "", responseAudioPath: "", headers: "" },
        model_ref: { modelRef: "", voice: "", format: "mp3", speed: "" },
    },
};

export const AUDIO_VOICE_PRESET_TABLE = audioVoicePresets as AudioVoicePresetTable;

export function localizeAudioVoicePresets(key: string, t: (key: string) => string): { value: string; label: string }[] {
    const provider = AUDIO_VOICE_PRESET_TABLE.providers?.[key];
    return (provider?.voices || []).map((voice) => ({
        value: voice.value,
        label: voice.labelKey ? t(voice.labelKey) : voice.label || voice.value,
    }));
}

export function resolveAudioVoicePresetKey(modelRef: string): string {
    const normalized = modelRef.toLowerCase();
    for (const [key, provider] of Object.entries(AUDIO_VOICE_PRESET_TABLE.providers || {})) {
        if (key === "edge-tts") continue;
        if ((provider.match || []).some((pattern) => normalized.includes(pattern.toLowerCase()))) {
            return key;
        }
    }
    return "";
}

export function voicePresetsForCustomTtsProtocol(protocol: string, t: (key: string) => string): { value: string; label: string }[] {
    if (protocol === "openai_speech") return localizeAudioVoicePresets("openai", t);
    if (protocol === "minimax_t2a_v2") return localizeAudioVoicePresets("minimax", t);
    return [];
}

export function headerValueForInput(value: string | Record<string, string> | undefined): string {
    if (!value) return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value);
}

export function sttEndpointPlaceholder(protocol: string): string {
    if (protocol === "openai_transcription") return "https://api.openai.com/v1/audio/transcriptions";
    if (protocol === "json_base64") return "https://example.com/transcribe-json";
    return "https://example.com/transcribe";
}

export function ttsEndpointPlaceholder(protocol: string): string {
    if (protocol === "openai_speech") return "https://api.openai.com/v1/audio/speech";
    if (protocol === "minimax_t2a_v2") return "https://api.minimaxi.com/v1/t2a_v2";
    return "https://example.com/tts";
}

export function mergeAudioConfig(value: unknown): AudioRuntimeConfig {
    const incoming = value && typeof value === "object" ? value as Partial<AudioRuntimeConfig> : {};
    const stt: Partial<AudioRuntimeConfig["stt"]> = incoming.stt || {};
    const tts: Partial<AudioRuntimeConfig["tts"]> = incoming.tts || {};
    return {
        stt: {
            ...DEFAULT_AUDIO_CONFIG.stt,
            ...stt,
            providers: {
                ...DEFAULT_AUDIO_CONFIG.stt.providers,
                ...(stt.providers || {}),
            },
            model_ref: {
                ...DEFAULT_AUDIO_CONFIG.stt.model_ref,
                ...(stt.model_ref || {}),
            },
        },
        tts: {
            ...DEFAULT_AUDIO_CONFIG.tts,
            ...tts,
            edge_tts: {
                ...DEFAULT_AUDIO_CONFIG.tts.edge_tts,
                ...(tts.edge_tts || {}),
            },
            custom: {
                ...DEFAULT_AUDIO_CONFIG.tts.custom,
                ...(tts.custom || {}),
            },
            model_ref: {
                ...DEFAULT_AUDIO_CONFIG.tts.model_ref,
                ...(tts.model_ref || {}),
            },
        },
    };
}

export function hasAudioInputCapability(model: AIModel): boolean {
    const ref = `${modelRefFor(model)} ${model.modelId || ""}`.toLowerCase();
    return ref.includes("stt")
        || ref.includes("asr")
        || ref.includes("whisper")
        || ref.includes("transcribe")
        || ref.includes("speech-to-text")
        || ref.includes("live-audio");
}

export function hasAudioOutputCapability(model: AIModel, controlMeta?: ControlPlaneModel | null): boolean {
    const type = normalizeModelType(model.type || controlMeta?.type);
    const capabilities = controlMeta?.capabilities;
    const ref = `${modelRefFor(model)} ${model.modelId || ""}`.toLowerCase();
    return TTS_MODEL_TYPES.has(type) || Boolean(capabilities?.audio || capabilities?.voice) || ref.includes("tts") || ref.includes("live-audio");
}

export function voiceManagerErrorText(payload: Record<string, unknown>, fallback: string): string {
    const message = extractErrorText(payload.error, fallback);
    const providerCode = typeof payload.providerCode === "string" ? payload.providerCode.trim() : "";
    const traceId = typeof payload.traceId === "string" ? payload.traceId.trim() : "";
    return [message, providerCode ? `code ${providerCode}` : "", traceId ? `trace ${traceId}` : ""]
        .filter(Boolean)
        .join(" · ");
}
