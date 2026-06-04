type AdminApiPerfSample = {
    route: string;
    status: number;
    elapsedMs: number;
    payloadBytes: number;
    engineElapsedMs?: number;
    at: number;
};

type PhonePerfSample = {
    stage: string;
    at: number;
    sessionId?: string;
    elapsedMs?: number;
    payloadBytes?: number;
    messageCount?: number;
    runtimeEventCount?: number;
    processCount?: number;
    metadata?: Record<string, unknown>;
};

type GlobalWithPerfSamples = typeof globalThis & {
    __v8ClientApiPerfSamples?: AdminApiPerfSample[];
    __v8PhonePerfSamples?: PhonePerfSample[];
};

const MAX_SAMPLES = 1000;
const MAX_PHONE_SAMPLES = 1000;

function samplesStore() {
    const store = globalThis as GlobalWithPerfSamples;
    store.__v8ClientApiPerfSamples ||= [];
    return store.__v8ClientApiPerfSamples;
}

function phoneSamplesStore() {
    const store = globalThis as GlobalWithPerfSamples;
    store.__v8PhonePerfSamples ||= [];
    return store.__v8PhonePerfSamples;
}

function percentile(values: number[], percentileValue: number) {
    const sorted = values.filter((item) => Number.isFinite(item)).sort((a, b) => a - b);
    if (sorted.length === 0) {
        return 0;
    }
    const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((percentileValue / 100) * sorted.length) - 1));
    return Math.round(sorted[index]);
}

export function jsonSizeBytes(value: unknown) {
    try {
        return Buffer.byteLength(JSON.stringify(value), "utf8");
    } catch {
        return 0;
    }
}

export function readEngineElapsedMs(payload: unknown) {
    const root = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
    const profile = root._profile && typeof root._profile === "object" ? root._profile as Record<string, unknown> : {};
    const elapsed = Number(profile.elapsedMs);
    return Number.isFinite(elapsed) ? elapsed : undefined;
}

export function recordAdminApiMetric(sample: Omit<AdminApiPerfSample, "at">) {
    const samples = samplesStore();
    samples.push({ ...sample, at: Date.now() });
    if (samples.length > MAX_SAMPLES) {
        samples.splice(0, samples.length - MAX_SAMPLES);
    }
}

function readFiniteNumber(value: unknown) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
}

function summarizePhonePerfSamples(samples: PhonePerfSample[]) {
    const byStage = new Map<string, PhonePerfSample[]>();
    for (const sample of samples) {
        const stage = String(sample.stage || "unknown").trim() || "unknown";
        const stageSamples = byStage.get(stage) || [];
        stageSamples.push(sample);
        byStage.set(stage, stageSamples);
    }
    return [...byStage.entries()].map(([stage, stageSamples]) => {
        const elapsed = stageSamples
            .map((sample) => sample.elapsedMs)
            .filter((item): item is number => typeof item === "number" && Number.isFinite(item));
        const payloadBytes = stageSamples
            .map((sample) => sample.payloadBytes)
            .filter((item): item is number => typeof item === "number" && Number.isFinite(item));
        return {
            stage,
            count: stageSamples.length,
            p50Ms: percentile(elapsed, 50),
            p95Ms: percentile(elapsed, 95),
            payloadP95Bytes: percentile(payloadBytes, 95),
            lastAt: stageSamples[stageSamples.length - 1]?.at || 0,
        };
    }).sort((a, b) => b.p95Ms - a.p95Ms || b.count - a.count);
}

export function recordPhonePerfMetric(input: Record<string, unknown>) {
    const stage = String(input.stage || "").trim();
    if (!stage) {
        return false;
    }
    const metadata = input.metadata && typeof input.metadata === "object" && !Array.isArray(input.metadata)
        ? input.metadata as Record<string, unknown>
        : {};
    const sample: PhonePerfSample = {
        stage,
        at: readFiniteNumber(input.at) || Date.now(),
        sessionId: typeof input.sessionId === "string" ? input.sessionId : undefined,
        elapsedMs: readFiniteNumber(input.elapsedMs),
        payloadBytes: readFiniteNumber(input.payloadBytes),
        messageCount: readFiniteNumber(input.messageCount),
        runtimeEventCount: readFiniteNumber(input.runtimeEventCount),
        processCount: readFiniteNumber(input.processCount),
        metadata,
    };
    const samples = phoneSamplesStore();
    samples.push(sample);
    if (samples.length > MAX_PHONE_SAMPLES) {
        samples.splice(0, samples.length - MAX_PHONE_SAMPLES);
    }
    return true;
}

export function getAdminApiPerfSnapshot() {
    const samples = samplesStore();
    const phoneSamples = phoneSamplesStore();
    const byRoute = new Map<string, AdminApiPerfSample[]>();
    for (const sample of samples) {
        const routeSamples = byRoute.get(sample.route) || [];
        routeSamples.push(sample);
        byRoute.set(sample.route, routeSamples);
    }
    return {
        sampleCount: samples.length,
        phone: {
            sampleCount: phoneSamples.length,
            stages: summarizePhonePerfSamples(phoneSamples),
            recent: phoneSamples.slice(-50),
        },
        routes: [...byRoute.entries()].map(([route, routeSamples]) => {
            const elapsed = routeSamples.map((sample) => sample.elapsedMs);
            const bytes = routeSamples.map((sample) => sample.payloadBytes);
            const engineElapsed = routeSamples
                .map((sample) => sample.engineElapsedMs)
                .filter((item): item is number => typeof item === "number" && Number.isFinite(item));
            return {
                route,
                count: routeSamples.length,
                p50Ms: percentile(elapsed, 50),
                p95Ms: percentile(elapsed, 95),
                payloadP95Bytes: percentile(bytes, 95),
                engineP95Ms: percentile(engineElapsed, 95),
                lastStatus: routeSamples[routeSamples.length - 1]?.status || 0,
                lastAt: routeSamples[routeSamples.length - 1]?.at || 0,
            };
        }).sort((a, b) => b.p95Ms - a.p95Ms),
    };
}
