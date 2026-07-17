import { randomBytes, randomUUID } from "crypto";
import fs from "fs/promises";
import os from "os";
import path from "path";
import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { parseModelRef } from "@/lib/models/model-admin";
import { resolveEngineBaseUrl, resolveReachableAdminPublicBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();
const VOICE_LEDGER_PATH = path.join(os.homedir(), ".v8-agent-os", "audio_voice_ledger.json");
const VOICE_SAMPLE_DIR = path.join(os.homedir(), ".v8-agent-os", "tmp", "voice-samples");
const VOICE_SAMPLE_TTL_MS = 30 * 60 * 1000;
const MAX_SAMPLE_BYTES = 20 * 1024 * 1024;

type VoiceAction = "capabilities" | "list" | "delete" | "clone_from_upload";
type VoiceSource = "remote" | "preset" | "local_ledger";

type VoiceCapabilities = {
    supportsVoiceManager: boolean;
    supportsList: boolean;
    supportsDelete: boolean;
    supportsCloneUpload: boolean;
};

type VoiceOption = {
    value: string;
    label: string;
    group?: string;
    deletable?: boolean;
    source?: VoiceSource;
};

type VoiceLedgerEntry = {
    provider: string;
    modelRef: string;
    voiceId: string;
    label: string;
    group?: string;
    createdAt: string;
};

type EngineProviderRecord = {
    provider?: Record<string, unknown>;
    models?: Record<string, unknown>;
};

type VoiceAdapterContext = {
    provider: "minimax_tts" | "aliyun_bailian_cosyvoice" | "volcengine_doubao_voice";
    capabilities: VoiceCapabilities;
    modelRef: string;
    modelId: string;
    providerMeta: Record<string, unknown>;
    modelMeta: Record<string, unknown>;
    mediaLimits: Record<string, unknown>;
    apiKey: string;
    baseUrl: string;
    providerModelId: string;
};

const ALIYUN_PRESET_VOICES: VoiceOption[] = [
    { value: "longxiaochun", label: "龙小淳 · longxiaochun", group: "preset", source: "preset" },
    { value: "longwan", label: "龙婉 · longwan", group: "preset", source: "preset" },
    { value: "longcheng", label: "龙橙 · longcheng", group: "preset", source: "preset" },
    { value: "longhua", label: "龙华 · longhua", group: "preset", source: "preset" },
    { value: "longxiaoxia", label: "龙小夏 · longxiaoxia", group: "preset", source: "preset" },
];

const VOLCENGINE_PRESET_VOICES: VoiceOption[] = [
    { value: "zh_female_shuangkuaisisi_moon_bigtts", label: "爽快思思 · zh_female_shuangkuaisisi_moon_bigtts", group: "preset", source: "preset" },
    { value: "zh_female_wanwanxiaohe_moon_bigtts", label: "湾湾小何 · zh_female_wanwanxiaohe_moon_bigtts", group: "preset", source: "preset" },
    { value: "zh_male_wennuanahu_moon_bigtts", label: "温暖阿虎 · zh_male_wennuanahu_moon_bigtts", group: "preset", source: "preset" },
    { value: "zh_male_shaonianzixin_moon_bigtts", label: "少年梓辛 · zh_male_shaonianzixin_moon_bigtts", group: "preset", source: "preset" },
];

function asObject(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function getString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function normalizeBaseUrl(value: unknown, fallback: string): string {
    return (getString(value) || fallback).replace(/\/+$/, "");
}

function voiceResponse(context: VoiceAdapterContext, payload: Record<string, unknown> = {}, status = 200) {
    return NextResponse.json(
        {
            provider: context.provider,
            capabilities: context.capabilities,
            ...payload,
        },
        { status },
    );
}

function errorResponse(message: string, status = 400, context?: VoiceAdapterContext) {
    return NextResponse.json(
        {
            ...(context ? { provider: context.provider, capabilities: context.capabilities } : {}),
            ok: false,
            error: message,
        },
        { status },
    );
}

function nestedString(source: Record<string, unknown>, paths: string[][]): string {
    for (const segments of paths) {
        let cursor: unknown = source;
        for (const segment of segments) {
            cursor = asObject(cursor)[segment];
        }
        const value = getString(cursor);
        if (value) return value;
    }
    return "";
}

function extractError(payload: unknown, fallback: string): string {
    const source = asObject(payload);
    const baseResp = asObject(source.base_resp || source.baseResp);
    const data = asObject(source.data);
    const dataBaseResp = asObject(data.base_resp || data.baseResp);
    return getString(baseResp.status_msg)
        || getString(baseResp.message)
        || getString(dataBaseResp.status_msg)
        || getString(source.status_text)
        || getString(source.statusText)
        || getString(source.error)
        || getString(source.message)
        || fallback;
}

function dedupeVoices(voices: VoiceOption[]): VoiceOption[] {
    const seen = new Set<string>();
    const result: VoiceOption[] = [];
    for (const voice of voices) {
        if (!voice.value || seen.has(voice.value)) continue;
        seen.add(voice.value);
        result.push(voice);
    }
    return result;
}

function flattenMiniMaxVoices(payload: unknown): VoiceOption[] {
    const root = asObject(payload);
    const source = asObject(root.data);
    const candidates = Object.keys(source).length > 0 ? source : root;
    const groups: Array<{ key: string; label: string; deletable: boolean }> = [
        { key: "system_voice", label: "system", deletable: false },
        { key: "voice_cloning", label: "cloned", deletable: true },
        { key: "voice_generation", label: "generated", deletable: true },
    ];
    const voices: VoiceOption[] = [];
    for (const group of groups) {
        const items = Array.isArray(candidates[group.key]) ? candidates[group.key] as Record<string, unknown>[] : [];
        for (const item of items) {
            const voiceId = getString(item.voice_id || item.voiceId || item.id);
            if (!voiceId) continue;
            const name = getString(item.voice_name || item.voiceName || item.name) || voiceId;
            voices.push({
                value: voiceId,
                label: `${name} · ${voiceId}`,
                group: group.label,
                deletable: group.deletable,
                source: "remote",
            });
        }
    }
    return voices;
}

async function readEngineModels(): Promise<Record<string, EngineProviderRecord>> {
    const response = await fetch(`${ENGINE_URL}/models/public`, { cache: "no-store" });
    if (!response.ok) {
        throw new Error(`Engine models API returned ${response.status}`);
    }
    const payload = await response.json().catch(() => ({}));
    return asObject(asObject(payload).providers) as Record<string, EngineProviderRecord>;
}

function detectAdapter(
    providerId: string,
    modelId: string,
    providerMeta: Record<string, unknown>,
    modelMeta: Record<string, unknown>,
    mediaLimits: Record<string, unknown>,
): VoiceAdapterContext["provider"] | "" {
    const adapterProviderId = getString(mediaLimits.adapterProviderId);
    const apiStandard = getString(mediaLimits.apiStandard);
    const parameterProfile = getString(modelMeta.parameterProfile);
    const probe = [
        providerId,
        modelId,
        getString(providerMeta.name),
        adapterProviderId,
        apiStandard,
        parameterProfile,
    ].join(" ").toLowerCase();
    if (adapterProviderId === "minimax_tts" || apiStandard === "minimax_tts" || parameterProfile === "minimax_tts") return "minimax_tts";
    if (adapterProviderId === "aliyun_bailian_cosyvoice" || apiStandard === "dashscope_cosyvoice_tts" || parameterProfile === "dashscope_cosyvoice_tts") return "aliyun_bailian_cosyvoice";
    if (adapterProviderId === "volcengine_doubao_voice" || apiStandard === "volcengine_ark_voice" || parameterProfile === "volcengine_ark_voice") return "volcengine_doubao_voice";
    if (probe.includes("minimax") && (probe.includes("t2a") || probe.includes("speech"))) return "minimax_tts";
    if ((probe.includes("aliyun") || probe.includes("dashscope") || probe.includes("bailian")) && probe.includes("cosyvoice")) return "aliyun_bailian_cosyvoice";
    if ((probe.includes("volcengine") || probe.includes("doubao")) && probe.includes("voice")) return "volcengine_doubao_voice";
    return "";
}

async function resolveContext(modelRef: string): Promise<VoiceAdapterContext> {
    const parsed = parseModelRef(modelRef);
    if (!parsed) {
        throw new Error("modelRef is required.");
    }
    const providers = await readEngineModels();
    const container = providers[parsed.providerId];
    if (!container) {
        throw new Error("Configured provider was not found.");
    }
    const providerMeta = asObject(container.provider);
    const modelMeta = asObject(asObject(container.models)[parsed.modelId]);
    if (Object.keys(modelMeta).length === 0) {
        throw new Error("Configured model was not found.");
    }
    const mediaLimits = asObject(modelMeta.mediaLimits);
    const provider = detectAdapter(parsed.providerId, parsed.modelId, providerMeta, modelMeta, mediaLimits);
    if (!provider) {
        throw new Error("Selected model does not expose TTS voice-management capabilities.");
    }
    const capabilities: VoiceCapabilities = {
        supportsVoiceManager: true,
        supportsList: true,
        supportsDelete: true,
        supportsCloneUpload: true,
    };
    if (provider === "volcengine_doubao_voice") {
        capabilities.supportsDelete = false;
    }
    return {
        provider,
        capabilities,
        modelRef,
        modelId: parsed.modelId,
        providerMeta,
        modelMeta,
        mediaLimits,
        apiKey: getString(providerMeta.api_key || providerMeta.apiKey),
        baseUrl: normalizeBaseUrl(providerMeta.base_url || providerMeta.baseUrl, provider === "minimax_tts" ? "https://api.minimaxi.com/v1" : ""),
        providerModelId: getString(mediaLimits.providerModelId) || parsed.modelId.split("/").filter(Boolean).pop() || parsed.modelId,
    };
}

function miniMaxBaseUrl(context: VoiceAdapterContext) {
    const trimmed = normalizeBaseUrl(context.baseUrl, "https://api.minimaxi.com/v1");
    const versionMatch = trimmed.match(/^(.*?\/v1)(?:\/.*)?$/i);
    if (versionMatch?.[1]) return versionMatch[1].replace(/\/+$/, "");
    return `${trimmed}/v1`;
}

function miniMaxEndpoint(context: VoiceAdapterContext, endpointPath: string) {
    return `${miniMaxBaseUrl(context)}/${endpointPath.replace(/^\/+/, "")}`;
}

function miniMaxTtsModelName(modelId: string): string {
    const leaf = getString(modelId).split("/").filter(Boolean).pop() || "";
    return leaf.startsWith("speech-") ? leaf : "speech-2.8-hd";
}

function assertApiKey(context: VoiceAdapterContext) {
    if (!context.apiKey || context.apiKey.includes("***") || context.apiKey.startsWith("oauth:")) {
        throw new Error(`${context.provider} API key is missing or not available to the server proxy.`);
    }
}

async function listMiniMaxVoices(context: VoiceAdapterContext) {
    assertApiKey(context);
    const response = await fetch(miniMaxEndpoint(context, "get_voice"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${context.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ voice_type: "all" }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return errorResponse(extractError(payload, `HTTP ${response.status}`), response.status, context);
    }
    return voiceResponse(context, { ok: true, voices: flattenMiniMaxVoices(payload) });
}

async function deleteMiniMaxVoice(context: VoiceAdapterContext, voiceId: string, voiceType: string) {
    assertApiKey(context);
    const response = await fetch(miniMaxEndpoint(context, "delete_voice"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${context.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            voice_id: voiceId,
            voice_type: voiceType || "voice_cloning",
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return errorResponse(extractError(payload, `HTTP ${response.status}`), response.status, context);
    }
    return voiceResponse(context, { ok: true, voiceId });
}

async function cloneMiniMaxVoice(context: VoiceAdapterContext, formData: FormData) {
    assertApiKey(context);
    const voiceId = getString(formData.get("voiceId"));
    const previewText = getString(formData.get("previewText"));
    const file = formData.get("file");
    if (!voiceId) return errorResponse("voiceId is required.", 400, context);
    if (!(file instanceof File)) return errorResponse("Sample audio file is required.", 400, context);

    const uploadForm = new FormData();
    uploadForm.append("purpose", "voice_clone");
    uploadForm.append("file", file, file.name || "sample-audio");
    const uploadResponse = await fetch(miniMaxEndpoint(context, "files/upload"), {
        method: "POST",
        headers: { Authorization: `Bearer ${context.apiKey}` },
        body: uploadForm,
    });
    const uploadPayload = await uploadResponse.json().catch(() => ({}));
    if (!uploadResponse.ok) {
        return errorResponse(extractError(uploadPayload, `HTTP ${uploadResponse.status}`), uploadResponse.status, context);
    }
    const fileId = nestedString(asObject(uploadPayload), [
        ["file", "file_id"],
        ["file", "id"],
        ["data", "file_id"],
        ["data", "file", "file_id"],
        ["file_id"],
    ]);
    if (!fileId) {
        return errorResponse("MiniMax upload succeeded but no file_id was returned.", 502, context);
    }

    const cloneBody: Record<string, unknown> = { file_id: fileId, voice_id: voiceId };
    if (previewText) {
        cloneBody.text = previewText;
        cloneBody.model = miniMaxTtsModelName(context.modelId);
    }
    const cloneResponse = await fetch(miniMaxEndpoint(context, "voice_clone"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${context.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(cloneBody),
    });
    const clonePayload = await cloneResponse.json().catch(() => ({}));
    if (!cloneResponse.ok) {
        return errorResponse(extractError(clonePayload, `HTTP ${cloneResponse.status}`), cloneResponse.status, context);
    }
    return voiceResponse(context, { ok: true, fileId, voiceId, clone: clonePayload });
}

async function readLedger(): Promise<VoiceLedgerEntry[]> {
    try {
        const raw = await fs.readFile(VOICE_LEDGER_PATH, "utf8");
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item === "object") as VoiceLedgerEntry[] : [];
    } catch {
        return [];
    }
}

async function writeLedger(entries: VoiceLedgerEntry[]) {
    await fs.mkdir(path.dirname(VOICE_LEDGER_PATH), { recursive: true });
    await fs.writeFile(VOICE_LEDGER_PATH, JSON.stringify(entries, null, 2), "utf8");
}

async function upsertLedgerEntry(entry: VoiceLedgerEntry) {
    const entries = await readLedger();
    const filtered = entries.filter((item) => !(item.provider === entry.provider && item.modelRef === entry.modelRef && item.voiceId === entry.voiceId));
    filtered.push(entry);
    await writeLedger(filtered);
}

async function removeLedgerEntry(context: VoiceAdapterContext, voiceId: string) {
    const entries = await readLedger();
    await writeLedger(entries.filter((item) => !(item.provider === context.provider && item.modelRef === context.modelRef && item.voiceId === voiceId)));
}

async function ledgerVoices(context: VoiceAdapterContext): Promise<VoiceOption[]> {
    const entries = await readLedger();
    return entries
        .filter((item) => item.provider === context.provider && item.modelRef === context.modelRef)
        .map((item) => ({
            value: item.voiceId,
            label: item.label || `${item.voiceId}`,
            group: item.group || "custom",
            deletable: context.capabilities.supportsDelete,
            source: "local_ledger" as const,
        }));
}

async function cleanupExpiredVoiceSamples() {
    const now = Date.now();
    let entries: string[] = [];
    try {
        entries = await fs.readdir(VOICE_SAMPLE_DIR);
    } catch {
        return;
    }
    await Promise.all(entries.map(async (name) => {
        const expiresAt = Number(name.split("-", 1)[0]);
        if (!Number.isFinite(expiresAt) || expiresAt > now) return;
        try {
            await fs.rm(path.join(VOICE_SAMPLE_DIR, name), { force: true });
        } catch {
            // Best effort cleanup only.
        }
    }));
}

function isPrivateHost(hostname: string) {
    const host = hostname.toLowerCase();
    if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return true;
    if (host === "::1" || host === "0.0.0.0" || host.startsWith("127.")) return true;
    if (/^10\./.test(host) || /^192\.168\./.test(host)) return true;
    const match = host.match(/^172\.(\d+)\./);
    return Boolean(match && Number(match[1]) >= 16 && Number(match[1]) <= 31);
}

function publicAdminBaseUrl() {
    const base = resolveReachableAdminPublicBaseUrl();
    if (!base) {
        throw new Error("阿里云声音复刻需要公网可访问的 Admin base URL，请配置 systemBase.bridge.adminBaseUrl。");
    }
    let parsed: URL;
    try {
        parsed = new URL(base);
    } catch {
        throw new Error("systemBase.bridge.adminBaseUrl 不是合法 URL，无法生成公网样本地址。");
    }
    if (isPrivateHost(parsed.hostname)) {
        throw new Error("阿里云声音复刻需要公网可访问的 Admin base URL，localhost、127.0.0.1 和内网地址不可用于厂商拉取样本；请配置 systemBase.bridge.adminBaseUrl。");
    }
    return base.replace(/\/+$/, "");
}

async function savePublicVoiceSample(file: File) {
    if (!file.type.startsWith("audio/")) {
        throw new Error("Only audio/* sample files are accepted.");
    }
    if (file.size > MAX_SAMPLE_BYTES) {
        throw new Error("Sample audio file exceeds the 20MB limit.");
    }
    const publicBaseUrl = publicAdminBaseUrl();
    await cleanupExpiredVoiceSamples();
    await fs.mkdir(VOICE_SAMPLE_DIR, { recursive: true });
    const token = randomBytes(24).toString("hex");
    const expiresAt = Date.now() + VOICE_SAMPLE_TTL_MS;
    const extension = path.extname(file.name || "") || ".audio";
    const filename = `${expiresAt}-${token}${extension}`;
    await fs.writeFile(path.join(VOICE_SAMPLE_DIR, filename), Buffer.from(await file.arrayBuffer()));
    return `${publicBaseUrl}/api/audio/voice-samples/${token}`;
}

function aliyunEndpoint(context: VoiceAdapterContext, suffix: string) {
    let base = normalizeBaseUrl(context.baseUrl, "https://dashscope.aliyuncs.com/api/v1");
    if (base.endsWith("/compatible-mode/v1")) {
        base = `${base.slice(0, -"/compatible-mode/v1".length)}/api/v1`;
    } else if (base.endsWith("/compatible-mode")) {
        base = `${base.slice(0, -"/compatible-mode".length)}/api/v1`;
    } else if (!base.includes("/api/v1")) {
        base = `${base}/api/v1`;
    }
    return `${base}/${suffix.replace(/^\/+/, "")}`;
}

async function listAliyunVoices(context: VoiceAdapterContext) {
    const voices = dedupeVoices([...ALIYUN_PRESET_VOICES, ...await ledgerVoices(context)]);
    return voiceResponse(context, { ok: true, voices });
}

async function deleteAliyunVoice(context: VoiceAdapterContext, voiceId: string) {
    assertApiKey(context);
    const response = await fetch(aliyunEndpoint(context, "/services/audio/tts/customization"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${context.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            model: "voice-enrollment",
            input: {
                action: "delete_voice",
                voice_id: voiceId,
            },
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return errorResponse(extractError(payload, `HTTP ${response.status}`), response.status, context);
    }
    await removeLedgerEntry(context, voiceId);
    return voiceResponse(context, { ok: true, voiceId });
}

async function cloneAliyunVoice(context: VoiceAdapterContext, formData: FormData) {
    assertApiKey(context);
    const requestedVoiceId = getString(formData.get("voiceId"));
    const file = formData.get("file");
    if (!requestedVoiceId) return errorResponse("voiceId is required.", 400, context);
    if (!(file instanceof File)) return errorResponse("Sample audio file is required.", 400, context);
    if (!file.type.startsWith("audio/")) return errorResponse("Only audio/* sample files are accepted.", 400, context);
    if (file.size > MAX_SAMPLE_BYTES) return errorResponse("Sample audio file exceeds the 20MB limit.", 400, context);

    let sampleUrl = "";
    try {
        sampleUrl = await savePublicVoiceSample(file);
    } catch (error: unknown) {
        return errorResponse(error instanceof Error ? error.message : "Unable to publish sample audio.", 400, context);
    }
    const response = await fetch(aliyunEndpoint(context, "/services/audio/tts/customization"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${context.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            model: "voice-enrollment",
            input: {
                action: "create_voice",
                target_model: "cosyvoice-v3.5-plus",
                prefix: requestedVoiceId,
                url: sampleUrl,
                language_hints: ["zh"],
            },
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return errorResponse(extractError(payload, `HTTP ${response.status}`), response.status, context);
    }
    const voiceId = nestedString(asObject(payload), [
        ["output", "voice_id"],
        ["output", "voiceId"],
        ["data", "voice_id"],
        ["voice_id"],
        ["voiceId"],
    ]) || requestedVoiceId;
    await upsertLedgerEntry({
        provider: context.provider,
        modelRef: context.modelRef,
        voiceId,
        label: `${voiceId}`,
        group: "custom",
        createdAt: new Date().toISOString(),
    });
    return voiceResponse(context, { ok: true, voiceId, clone: payload });
}

function volcengineHeaders(context: VoiceAdapterContext, resourceId?: string) {
    const appId = getString(context.providerMeta.voice_app_id || context.providerMeta.voiceAppId);
    const accessKey = context.apiKey;
    const selectedResourceId = getString(resourceId) || getString(context.providerMeta.voice_resource_id || context.providerMeta.voiceResourceId);
    if (!appId) throw new Error("火山豆包语音缺少 provider.voice_app_id。");
    if (!accessKey) throw new Error("火山豆包语音缺少 Access Key/API Key。");
    if (!selectedResourceId) throw new Error("火山豆包语音缺少 provider.voice_resource_id。");
    return {
        "Content-Type": "application/json",
        "X-Api-App-Key": appId,
        "X-Api-Access-Key": accessKey,
        "X-Api-Resource-Id": selectedResourceId,
        "X-Api-Connect-Id": randomUUID(),
    };
}

function volcengineEndpoint(context: VoiceAdapterContext, suffix: string) {
    const base = normalizeBaseUrl(context.baseUrl, "https://openspeech.bytedance.com/api/v3/tts");
    if (base.includes("/api/v3/tts/")) {
        return `${base.split("/api/v3/tts/", 1)[0]}/api/v3/tts/${suffix.replace(/^\/+/, "")}`;
    }
    if (base.endsWith("/api/v3")) {
        return `${base}/tts/${suffix.replace(/^\/+/, "")}`;
    }
    if (base.endsWith("/api/v3/tts")) {
        return `${base}/${suffix.replace(/^\/+/, "")}`;
    }
    if (base.includes("volces.com/api/v3") || base.includes("ark.cn-")) {
        return `https://openspeech.bytedance.com/api/v3/tts/${suffix.replace(/^\/+/, "")}`;
    }
    return `${base}/${suffix.replace(/^\/+/, "")}`;
}

async function listVolcengineVoices(context: VoiceAdapterContext) {
    const localVoices = await ledgerVoices(context);
    const refreshed: VoiceOption[] = [];
    for (const voice of localVoices) {
        try {
            const response = await fetch(volcengineEndpoint(context, "get_voice"), {
                method: "POST",
                headers: volcengineHeaders(context, "seed-icl-2.0"),
                body: JSON.stringify({ speaker_id: voice.value }),
            });
            const payload = await response.json().catch(() => ({}));
            const status = getString(asObject(payload).status) || getString(asObject(payload).message);
            refreshed.push({
                ...voice,
                label: status ? `${voice.label} · ${status}` : voice.label,
            });
        } catch {
            refreshed.push(voice);
        }
    }
    return voiceResponse(context, { ok: true, voices: dedupeVoices([...VOLCENGINE_PRESET_VOICES, ...refreshed]) });
}

async function cloneVolcengineVoice(context: VoiceAdapterContext, formData: FormData) {
    const speakerId = getString(formData.get("voiceId"));
    const file = formData.get("file");
    if (!speakerId) return errorResponse("voiceId is required.", 400, context);
    if (!(file instanceof File)) return errorResponse("Sample audio file is required.", 400, context);
    if (!file.type.startsWith("audio/")) return errorResponse("Only audio/* sample files are accepted.", 400, context);
    if (file.size > MAX_SAMPLE_BYTES) return errorResponse("Sample audio file exceeds the 20MB limit.", 400, context);

    const audioBase64 = Buffer.from(await file.arrayBuffer()).toString("base64");
    const response = await fetch(volcengineEndpoint(context, "voice_clone"), {
        method: "POST",
        headers: volcengineHeaders(context, "seed-icl-2.0"),
        body: JSON.stringify({
            speaker_id: speakerId,
            audio: audioBase64,
            language: 0,
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return errorResponse(extractError(payload, `HTTP ${response.status}`), response.status, context);
    }
    const voiceId = nestedString(asObject(payload), [
        ["data", "speaker_id"],
        ["speaker_id"],
        ["data", "speakerId"],
        ["speakerId"],
    ]) || speakerId;
    await upsertLedgerEntry({
        provider: context.provider,
        modelRef: context.modelRef,
        voiceId,
        label: `${voiceId}`,
        group: "custom",
        createdAt: new Date().toISOString(),
    });
    return voiceResponse(context, { ok: true, voiceId, clone: payload });
}

async function handleList(context: VoiceAdapterContext) {
    await cleanupExpiredVoiceSamples();
    if (context.provider === "minimax_tts") return listMiniMaxVoices(context);
    if (context.provider === "aliyun_bailian_cosyvoice") return listAliyunVoices(context);
    return listVolcengineVoices(context);
}

async function handleDelete(context: VoiceAdapterContext, voiceId: string, voiceType: string) {
    if (!voiceId) return errorResponse("voiceId is required.", 400, context);
    if (context.provider === "minimax_tts") return deleteMiniMaxVoice(context, voiceId, voiceType);
    if (context.provider === "aliyun_bailian_cosyvoice") return deleteAliyunVoice(context, voiceId);
    return errorResponse("Selected provider does not support deleting remote voices from Admin.", 400, context);
}

async function handleClone(context: VoiceAdapterContext, formData: FormData) {
    await cleanupExpiredVoiceSamples();
    if (context.provider === "minimax_tts") return cloneMiniMaxVoice(context, formData);
    if (context.provider === "aliyun_bailian_cosyvoice") return cloneAliyunVoice(context, formData);
    return cloneVolcengineVoice(context, formData);
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });

    try {
        const contentType = req.headers.get("content-type") || "";
        if (contentType.includes("multipart/form-data")) {
            const formData = await req.formData();
            const action = (getString(formData.get("action")) || "clone_from_upload") as VoiceAction;
            if (action !== "clone_from_upload") {
                return NextResponse.json({ ok: false, error: "Unsupported multipart action." }, { status: 400 });
            }
            const context = await resolveContext(getString(formData.get("modelRef")));
            return handleClone(context, formData);
        }

        const body = asObject(await req.json().catch(() => ({})));
        const action = (getString(body.action) || "list") as VoiceAction;
        const context = await resolveContext(getString(body.modelRef));
        if (action === "capabilities") return voiceResponse(context, { ok: true, voices: [] });
        if (action === "list") return handleList(context);
        if (action === "delete") return handleDelete(context, getString(body.voiceId), getString(body.voiceType));
        return errorResponse("Unsupported action.", 400, context);
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        return NextResponse.json({ ok: false, error: message }, { status: 500 });
    }
}
