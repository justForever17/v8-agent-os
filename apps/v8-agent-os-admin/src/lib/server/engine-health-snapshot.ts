import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export type EngineHealthSnapshot = {
    data: Record<string, unknown> | null;
    available: boolean | null;
    updatedAt: number;
    refreshing: boolean;
    stale: boolean;
    error: string | null;
};

type EngineHealthCache = Omit<EngineHealthSnapshot, "refreshing" | "stale"> & {
    lastAttemptAt: number;
};

const HEALTH_FRESH_TTL_MS = 5_000;
const HEALTH_RETRY_BACKOFF_MS = 5_000;
const HEALTH_REQUEST_TIMEOUT_MS = 4_000;

const healthCaches = new Map<string, EngineHealthCache>();
const healthRequests = new Map<string, Promise<EngineHealthCache>>();

function emptyHealthCache(): EngineHealthCache {
    return {
        data: null,
        available: null,
        updatedAt: 0,
        lastAttemptAt: 0,
        error: null,
    };
}

function healthCacheFor(origin: string) {
    return healthCaches.get(origin) || emptyHealthCache();
}

function errorMessage(error: unknown) {
    return error instanceof Error ? error.message : String(error || "engine_health_failed");
}

function startHealthRefresh(origin: string) {
    const existingRequest = healthRequests.get(origin);
    if (existingRequest) return existingRequest;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), HEALTH_REQUEST_TIMEOUT_MS);
    const startedAt = Date.now();
    healthCaches.set(origin, {
        ...healthCacheFor(origin),
        lastAttemptAt: startedAt,
    });

    const request = Promise.resolve().then(async () => {
        try {
            const response = await fetch(`${origin}/health`, {
                cache: "no-store",
                signal: controller.signal,
            });
            if (!response.ok) {
                throw new Error(`engine_status_${response.status}`);
            }
            const payload = await response.json().catch(() => ({}));
            const refreshed: EngineHealthCache = {
                data: payload && typeof payload === "object" ? payload as Record<string, unknown> : {},
                available: true,
                updatedAt: Date.now(),
                lastAttemptAt: startedAt,
                error: null,
            };
            healthCaches.set(origin, refreshed);
            return refreshed;
        } catch (error) {
            const failed: EngineHealthCache = {
                ...healthCacheFor(origin),
                available: false,
                lastAttemptAt: startedAt,
                error: errorMessage(error),
            };
            healthCaches.set(origin, failed);
            return failed;
        } finally {
            clearTimeout(timeoutId);
        }
    });

    healthRequests.set(origin, request);
    const clearRequest = () => {
        if (healthRequests.get(origin) === request) {
            healthRequests.delete(origin);
        }
    };
    void request.then(clearRequest, clearRequest);
    return request;
}

export async function readEngineHealthSnapshot(options: { force?: boolean; waitForFresh?: boolean } = {}): Promise<EngineHealthSnapshot> {
    let origin: string;
    try {
        origin = String(resolveEngineOrigin() || "").trim().replace(/\/+$/, "");
        if (!origin) throw new Error("engine_origin_missing");
    } catch (error) {
        return {
            data: null,
            available: false,
            updatedAt: 0,
            refreshing: false,
            stale: false,
            error: errorMessage(error),
        };
    }

    let healthCache = healthCacheFor(origin);
    let pending = healthRequests.get(origin);

    if (options.force && !pending) {
        pending = startHealthRefresh(origin);
    }
    if (options.waitForFresh && pending) {
        await pending;
        if (healthRequests.get(origin) === pending) {
            healthRequests.delete(origin);
        }
    }

    const refreshedAt = Date.now();
    healthCache = healthCacheFor(origin);
    const refreshedHasFreshData = healthCache.data !== null
        && refreshedAt - healthCache.updatedAt <= HEALTH_FRESH_TTL_MS;
    const refreshRecommended = !refreshedHasFreshData
        && refreshedAt - healthCache.lastAttemptAt >= HEALTH_RETRY_BACKOFF_MS;
    return {
        data: healthCache.data,
        available: healthCache.available,
        updatedAt: healthCache.updatedAt,
        refreshing: healthRequests.has(origin) || refreshRecommended,
        stale: healthCache.data !== null && refreshedAt - healthCache.updatedAt > HEALTH_FRESH_TTL_MS,
        error: healthCache.error,
    };
}
