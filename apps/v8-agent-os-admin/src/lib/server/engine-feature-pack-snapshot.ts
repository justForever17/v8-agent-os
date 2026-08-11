export type EngineFeaturePackSnapshot = {
    data: Record<string, unknown> | null;
    available: boolean | null;
    updatedAt: number;
    refreshing: boolean;
    stale: boolean;
    error: string | null;
};

type SnapshotCache = Omit<EngineFeaturePackSnapshot, "refreshing" | "stale">;

type SnapshotOptions = {
    origin: string;
    internalSecret: string;
    force?: boolean;
    fetchImpl?: typeof fetch;
    now?: () => number;
};

const SNAPSHOT_FRESH_TTL_MS = 5_000;
const SNAPSHOT_REQUEST_TIMEOUT_MS = 3_000;
const snapshotCaches = new Map<string, SnapshotCache>();
const snapshotRequests = new Map<string, Promise<SnapshotCache>>();

function emptySnapshotCache(): SnapshotCache {
    return {
        data: null,
        available: null,
        updatedAt: 0,
        error: null,
    };
}

function errorMessage(error: unknown) {
    return error instanceof Error ? error.message : String(error || "engine_feature_pack_status_failed");
}

function sampledAtMillis(payload: Record<string, unknown>) {
    const value = String(payload.sampledAt || "").trim();
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function cacheKey(origin: string) {
    return origin.trim().replace(/\/+$/, "");
}

function startSnapshotRequest(input: Required<Pick<SnapshotOptions, "origin" | "internalSecret" | "fetchImpl" | "now">>) {
    const key = cacheKey(input.origin);
    const pending = snapshotRequests.get(key);
    if (pending) return pending;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), SNAPSHOT_REQUEST_TIMEOUT_MS);
    const request = Promise.resolve().then(async () => {
        try {
            const response = await input.fetchImpl(`${key}/v1/runtime-feature-packs/status`, {
                cache: "no-store",
                headers: {
                    Accept: "application/json",
                    "x-v8-agent-os-secret": input.internalSecret,
                },
                signal: controller.signal,
            });
            if (!response.ok) throw new Error(`engine_feature_pack_status_${response.status}`);
            const payload = await response.json().catch(() => null);
            if (!payload || typeof payload !== "object" || !Array.isArray(payload.featurePacks)) {
                throw new Error("engine_feature_pack_status_invalid");
            }
            const sampledAt = sampledAtMillis(payload as Record<string, unknown>);
            if (sampledAt === null) throw new Error("engine_feature_pack_status_invalid");
            const refreshed: SnapshotCache = {
                data: payload as Record<string, unknown>,
                available: true,
                updatedAt: sampledAt,
                error: null,
            };
            snapshotCaches.set(key, refreshed);
            return refreshed;
        } catch (error) {
            const previous = snapshotCaches.get(key) || emptySnapshotCache();
            const failed: SnapshotCache = {
                ...previous,
                available: false,
                error: errorMessage(error),
            };
            snapshotCaches.set(key, failed);
            return failed;
        } finally {
            clearTimeout(timeoutId);
        }
    });

    snapshotRequests.set(key, request);
    const clearRequest = () => {
        if (snapshotRequests.get(key) === request) snapshotRequests.delete(key);
    };
    void request.then(clearRequest, clearRequest);
    return request;
}

export async function readEngineFeaturePackSnapshot(options: SnapshotOptions): Promise<EngineFeaturePackSnapshot> {
    const origin = cacheKey(String(options.origin || ""));
    const internalSecret = String(options.internalSecret || "").trim();
    const now = options.now || Date.now;
    if (!origin || !internalSecret) {
        return {
            data: null,
            available: false,
            updatedAt: 0,
            refreshing: false,
            stale: false,
            error: !origin ? "engine_origin_missing" : "engine_internal_secret_missing",
        };
    }

    const fetchImpl = options.fetchImpl || fetch;
    const cached = snapshotCaches.get(origin) || emptySnapshotCache();
    const fresh = cached.data !== null && now() - cached.updatedAt <= SNAPSHOT_FRESH_TTL_MS;
    let pending = snapshotRequests.get(origin);
    if ((options.force || !fresh) && !pending) {
        pending = startSnapshotRequest({ origin, internalSecret, fetchImpl, now });
    }
    if (pending) await pending;

    const resolvedAt = now();
    const resolved = snapshotCaches.get(origin) || emptySnapshotCache();
    return {
        data: resolved.data,
        available: resolved.available,
        updatedAt: resolved.updatedAt,
        refreshing: snapshotRequests.has(origin),
        stale: resolved.data !== null && resolvedAt - resolved.updatedAt > SNAPSHOT_FRESH_TTL_MS,
        error: resolved.error,
    };
}

export function resetEngineFeaturePackSnapshotForTests() {
    snapshotCaches.clear();
    snapshotRequests.clear();
}
