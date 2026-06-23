import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { parseModelRef } from "@/lib/models/model-admin";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

type MiniMaxVoiceEntry = {
    value: string;
    label: string;
    group?: string;
    deletable?: boolean;
};

type EngineProviderRecord = {
    provider?: Record<string, unknown>;
    models?: Record<string, unknown>;
};

function asObject(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function getString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function normalizeMiniMaxBaseUrl(value: unknown): string {
    const raw = getString(value) || "https://api.minimaxi.com/v1";
    const trimmed = raw.replace(/\/+$/, "");
    const versionMatch = trimmed.match(/^(.*?\/v1)(?:\/.*)?$/i);
    if (versionMatch?.[1]) return versionMatch[1].replace(/\/+$/, "");
    return `${trimmed}/v1`;
}

function miniMaxEndpoint(baseUrl: string, path: string): string {
    return `${baseUrl.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

function miniMaxTtsModelName(modelId: string): string {
    const leaf = getString(modelId).split("/").filter(Boolean).pop() || "";
    return leaf.startsWith("speech-") ? leaf : "speech-2.8-hd";
}

function extractMiniMaxMessage(payload: unknown, fallback: string): string {
    const source = asObject(payload);
    const baseResp = asObject(source.base_resp || source.baseResp);
    const data = asObject(source.data);
    const dataBaseResp = asObject(data.base_resp || data.baseResp);
    return getString(baseResp.status_msg)
        || getString(baseResp.message)
        || getString(dataBaseResp.status_msg)
        || getString(source.error)
        || getString(source.message)
        || fallback;
}

function nestedString(source: Record<string, unknown>, paths: string[][]): string {
    for (const path of paths) {
        let cursor: unknown = source;
        for (const part of path) {
            cursor = asObject(cursor)[part];
        }
        const value = getString(cursor);
        if (value) return value;
    }
    return "";
}

function flattenMiniMaxVoices(payload: unknown): MiniMaxVoiceEntry[] {
    const root = asObject(payload);
    const source = asObject(root.data);
    const candidates = Object.keys(source).length > 0 ? source : root;
    const groups: Array<{ key: string; label: string; deletable: boolean }> = [
        { key: "system_voice", label: "system", deletable: false },
        { key: "voice_cloning", label: "cloned", deletable: true },
        { key: "voice_generation", label: "generated", deletable: true },
    ];
    const voices: MiniMaxVoiceEntry[] = [];
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
            });
        }
    }
    return voices;
}

async function readEngineModels(): Promise<Record<string, EngineProviderRecord>> {
    const response = await fetch(`${ENGINE_URL}/models`, { cache: "no-store" });
    if (!response.ok) {
        throw new Error(`Engine models API returned ${response.status}`);
    }
    const payload = await response.json().catch(() => ({}));
    return asObject(asObject(payload).providers) as Record<string, EngineProviderRecord>;
}

async function resolveMiniMaxCredential(modelRef: string) {
    const parsed = parseModelRef(modelRef);
    if (!parsed) {
        throw new Error("modelRef is required.");
    }
    const providers = await readEngineModels();
    const container = providers[parsed.providerId];
    if (!container) {
        throw new Error("Configured provider was not found.");
    }
    const provider = asObject(container.provider);
    const providerName = getString(provider.name);
    const probeText = `${parsed.providerId} ${providerName} ${parsed.modelId}`.toLowerCase();
    if (!probeText.includes("minimax") || !(probeText.includes("t2a") || probeText.includes("speech"))) {
        throw new Error("Selected model is not a MiniMax TTS model.");
    }
    const apiKey = getString(provider.api_key || provider.apiKey);
    if (!apiKey || apiKey.includes("***") || apiKey.startsWith("oauth:")) {
        throw new Error("MiniMax API key is missing or not available to the server proxy.");
    }
    return {
        apiKey,
        baseUrl: normalizeMiniMaxBaseUrl(provider.base_url || provider.baseUrl),
        modelId: parsed.modelId,
    };
}

async function miniMaxJsonRequest(baseUrl: string, apiKey: string, path: string, body: Record<string, unknown>) {
    const response = await fetch(miniMaxEndpoint(baseUrl, path), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return NextResponse.json({ error: extractMiniMaxMessage(payload, `HTTP ${response.status}`) }, { status: response.status });
    }
    return NextResponse.json(payload);
}

async function listVoices(modelRef: string) {
    const credential = await resolveMiniMaxCredential(modelRef);
    const response = await fetch(miniMaxEndpoint(credential.baseUrl, "get_voice"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${credential.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ voice_type: "all" }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        return NextResponse.json({ error: extractMiniMaxMessage(payload, `HTTP ${response.status}`) }, { status: response.status });
    }
    return NextResponse.json({ voices: flattenMiniMaxVoices(payload) });
}

async function deleteVoice(modelRef: string, voiceId: string, voiceType: string) {
    if (!voiceId) {
        return NextResponse.json({ error: "voiceId is required." }, { status: 400 });
    }
    const credential = await resolveMiniMaxCredential(modelRef);
    return miniMaxJsonRequest(credential.baseUrl, credential.apiKey, "delete_voice", {
        voice_id: voiceId,
        voice_type: voiceType || "voice_cloning",
    });
}

async function cloneFromUpload(formData: FormData) {
    const modelRef = getString(formData.get("modelRef"));
    const voiceId = getString(formData.get("voiceId"));
    const previewText = getString(formData.get("previewText"));
    const file = formData.get("file");
    if (!modelRef || !voiceId) {
        return NextResponse.json({ error: "modelRef and voiceId are required." }, { status: 400 });
    }
    if (!(file instanceof File)) {
        return NextResponse.json({ error: "Sample audio file is required." }, { status: 400 });
    }

    const credential = await resolveMiniMaxCredential(modelRef);
    const uploadForm = new FormData();
    uploadForm.append("purpose", "voice_clone");
    uploadForm.append("file", file, file.name || "sample-audio");
    const uploadResponse = await fetch(miniMaxEndpoint(credential.baseUrl, "files/upload"), {
        method: "POST",
        headers: { Authorization: `Bearer ${credential.apiKey}` },
        body: uploadForm,
    });
    const uploadPayload = await uploadResponse.json().catch(() => ({}));
    if (!uploadResponse.ok) {
        return NextResponse.json({ error: extractMiniMaxMessage(uploadPayload, `HTTP ${uploadResponse.status}`) }, { status: uploadResponse.status });
    }
    const uploadSource = asObject(uploadPayload);
    const fileId = nestedString(uploadSource, [
        ["file", "file_id"],
        ["file", "id"],
        ["data", "file_id"],
        ["data", "file", "file_id"],
        ["file_id"],
    ]);
    if (!fileId) {
        return NextResponse.json({ error: "MiniMax upload succeeded but no file_id was returned.", upload: uploadPayload }, { status: 502 });
    }

    const cloneBody: Record<string, unknown> = {
        file_id: fileId,
        voice_id: voiceId,
    };
    if (previewText) {
        cloneBody.text = previewText;
        cloneBody.model = miniMaxTtsModelName(credential.modelId);
    }
    const cloneResponse = await fetch(miniMaxEndpoint(credential.baseUrl, "voice_clone"), {
        method: "POST",
        headers: {
            Authorization: `Bearer ${credential.apiKey}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(cloneBody),
    });
    const clonePayload = await cloneResponse.json().catch(() => ({}));
    if (!cloneResponse.ok) {
        return NextResponse.json({ error: extractMiniMaxMessage(clonePayload, `HTTP ${cloneResponse.status}`), fileId }, { status: cloneResponse.status });
    }
    return NextResponse.json({ ok: true, fileId, voiceId, clone: clonePayload });
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const contentType = req.headers.get("content-type") || "";
        if (contentType.includes("multipart/form-data")) {
            const formData = await req.formData();
            const action = getString(formData.get("action")) || "clone_from_upload";
            if (action !== "clone_from_upload") {
                return NextResponse.json({ error: "Unsupported multipart action." }, { status: 400 });
            }
            return cloneFromUpload(formData);
        }

        const body = asObject(await req.json().catch(() => ({})));
        const action = getString(body.action) || "list";
        const modelRef = getString(body.modelRef);
        if (action === "list") return listVoices(modelRef);
        if (action === "delete") return deleteVoice(modelRef, getString(body.voiceId), getString(body.voiceType));
        return NextResponse.json({ error: "Unsupported action." }, { status: 400 });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
