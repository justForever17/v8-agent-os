import { Platform } from "react-native";

type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

export type PhonePerfAuditSample = {
    stage: string;
    at: number;
    platform: string;
    sessionId?: string;
    elapsedMs?: number;
    payloadBytes?: number;
    messageCount?: number;
    runtimeEventCount?: number;
    processCount?: number;
    metadata?: Record<string, unknown>;
};

const MAX_BUFFERED_SAMPLES = 200;

const bufferedSamples: PhonePerfAuditSample[] = [];

function normalizeEnvFlag(value: unknown) {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "1" || normalized === "true" || normalized === "yes";
}

const STATIC_AUDIT_ENABLED = normalizeEnvFlag(process.env.EXPO_PUBLIC_V8_PHONE_PERF_AUDIT);
const STATIC_AUDIT_POST_ENABLED = normalizeEnvFlag(process.env.EXPO_PUBLIC_V8_PHONE_PERF_AUDIT_POST);

function readDynamicEnvFlag(name: string) {
    const globalProcess = (globalThis as unknown as { process?: { env?: Record<string, string | undefined> } }).process;
    return normalizeEnvFlag(globalProcess?.env?.[name]);
}

export function isPhonePerfAuditEnabled() {
    return STATIC_AUDIT_ENABLED || readDynamicEnvFlag("EXPO_PUBLIC_V8_PHONE_PERF_AUDIT");
}

export function shouldPostPhonePerfAuditSamples() {
    return STATIC_AUDIT_POST_ENABLED || readDynamicEnvFlag("EXPO_PUBLIC_V8_PHONE_PERF_AUDIT_POST");
}

function sanitizePayload(payload: Record<string, unknown>) {
    const next: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(payload || {})) {
        if (value === undefined || typeof value === "function") {
            continue;
        }
        next[key] = value;
    }
    return next;
}

export function emitPhonePerfAuditSample(stage: string, payload: Record<string, unknown> = {}) {
    if (!isPhonePerfAuditEnabled()) {
        return;
    }
    const sample = {
        ...sanitizePayload(payload),
        stage,
        at: Date.now(),
        platform: Platform.OS,
    } as PhonePerfAuditSample;
    bufferedSamples.push(sample);
    if (bufferedSamples.length > MAX_BUFFERED_SAMPLES) {
        bufferedSamples.splice(0, bufferedSamples.length - MAX_BUFFERED_SAMPLES);
    }
    try {
        console.log(`V8_PHONE_PERF ${JSON.stringify(sample)}`);
    } catch {
        // Audit logging must never affect the chat UI.
    }
}

export function drainPhonePerfAuditSamples() {
    if (!bufferedSamples.length) {
        return [];
    }
    return bufferedSamples.splice(0, bufferedSamples.length);
}

export async function postPhonePerfAuditSamples(authorizedFetch: AuthorizedFetch) {
    if (!shouldPostPhonePerfAuditSamples()) {
        return;
    }
    const samples = drainPhonePerfAuditSamples();
    if (!samples.length) {
        return;
    }
    try {
        const response = await authorizedFetch("/api/client/perf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ samples }),
        });
        if (!response.ok) {
            for (const sample of samples) {
                bufferedSamples.push(sample);
            }
        }
    } catch {
        for (const sample of samples) {
            bufferedSamples.push(sample);
        }
    }
    if (bufferedSamples.length > MAX_BUFFERED_SAMPLES) {
        bufferedSamples.splice(0, bufferedSamples.length - MAX_BUFFERED_SAMPLES);
    }
}
