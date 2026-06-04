import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { normalizeProcessForRealtimeSurface } from "@/lib/server/session-realtime-resource";

const ENGINE_URL = resolveEngineBaseUrl();
const PROCESS_SURFACE_TIMEOUT_MS = 3500;
const PROCESS_SURFACE_CACHE_TTL_MS = 45000;

type CachedProcessSurface = {
    currentRunId: string | null;
    latestSeq: number;
    processes: NonNullable<ReturnType<typeof normalizeProcessForRealtimeSurface>>[];
    lastUpdatedAt: number;
};

const processSurfaceCache = new Map<string, CachedProcessSurface>();

function staleProcessSurface(sessionId: string, reason: string) {
    const cached = processSurfaceCache.get(sessionId);
    const now = Date.now();
    const cacheAgeMs = cached ? Math.max(0, now - cached.lastUpdatedAt) : undefined;
    const usableCache = cached && cacheAgeMs !== undefined && cacheAgeMs <= PROCESS_SURFACE_CACHE_TTL_MS
        ? cached
        : null;
    return {
        sessionId,
        currentRunId: usableCache?.currentRunId || null,
        latestSeq: usableCache?.latestSeq || 0,
        processes: usableCache?.processes || [],
        stale: true,
        processPanelError: reason,
        lastUpdatedAt: usableCache ? new Date(usableCache.lastUpdatedAt).toISOString() : null,
        cacheAgeMs: usableCache ? cacheAgeMs : null,
        _profile: {
            stale: true,
            processPanelError: reason,
            cacheAgeMs: usableCache ? cacheAgeMs : null,
        },
    };
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const startedAt = Date.now();
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;
    let timeout: ReturnType<typeof setTimeout> | null = null;

    try {
        const controller = new AbortController();
        timeout = setTimeout(() => controller.abort(), PROCESS_SURFACE_TIMEOUT_MS);
        const response = await fetch(`${ENGINE_URL}/sessions/${id}/processes`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
            signal: controller.signal,
        });
        clearTimeout(timeout);

        if (!response.ok) {
            console.error("[Client Session Processes] Failed to fetch engine process surface:", await response.text());
            const responsePayload = staleProcessSurface(id, "engine_process_surface_unavailable");
            const elapsedMs = Date.now() - startedAt;
            recordAdminApiMetric({
                route: "client.sessions.processes",
                status: 200,
                elapsedMs,
                payloadBytes: jsonSizeBytes(responsePayload),
            });
            return NextResponse.json(responsePayload, {
                headers: {
                    "x-v8-admin-proxy-ms": String(elapsedMs),
                    "x-v8-payload-bytes": String(jsonSizeBytes(responsePayload)),
                    "x-v8-process-surface-stale": "1",
                },
            });
        }

        const payload = await response.json().catch(() => ({}));
        const processes = Array.isArray(payload?.processes)
            ? payload.processes
                .map((process: unknown) => normalizeProcessForRealtimeSurface(process))
                .filter((process): process is NonNullable<ReturnType<typeof normalizeProcessForRealtimeSurface>> => Boolean(process))
            : [];
        const now = Date.now();
        processSurfaceCache.set(id, {
            currentRunId: typeof payload?.currentRunId === "string" ? payload.currentRunId : null,
            latestSeq: Number(payload?.latestSeq || 0) || 0,
            processes,
            lastUpdatedAt: now,
        });
        const responsePayload = {
            sessionId: id,
            currentRunId: payload?.currentRunId || null,
            latestSeq: payload?.latestSeq || 0,
            processes,
            stale: false,
            lastUpdatedAt: new Date(now).toISOString(),
            cacheAgeMs: 0,
            _profile: payload?._profile,
        };
        const elapsedMs = Date.now() - startedAt;
        recordAdminApiMetric({
            route: "client.sessions.processes",
            status: response.status,
            elapsedMs,
            payloadBytes: jsonSizeBytes(responsePayload),
            engineElapsedMs: readEngineElapsedMs(payload),
        });

        return NextResponse.json(responsePayload, {
            headers: {
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(jsonSizeBytes(responsePayload)),
            },
        });
    } catch (error) {
        if (timeout) {
            clearTimeout(timeout);
        }
        console.error("[Client Session Processes] Engine communication failed:", error);
        const responsePayload = staleProcessSurface(id, "engine_process_surface_timeout");
        const elapsedMs = Date.now() - startedAt;
        recordAdminApiMetric({
            route: "client.sessions.processes",
            status: 200,
            elapsedMs,
            payloadBytes: jsonSizeBytes(responsePayload),
        });
        return NextResponse.json(responsePayload, {
            headers: {
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(jsonSizeBytes(responsePayload)),
                "x-v8-process-surface-stale": "1",
            },
        });
    }
}
