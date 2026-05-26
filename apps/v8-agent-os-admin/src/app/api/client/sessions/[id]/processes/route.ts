import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { normalizeProcessForRealtimeSurface } from "@/lib/server/session-realtime-resource";

const ENGINE_URL = resolveEngineBaseUrl();
const PROCESS_SURFACE_TIMEOUT_MS = 6000;

function staleProcessSurface(sessionId: string, reason: string) {
    return {
        sessionId,
        currentRunId: null,
        latestSeq: 0,
        processes: [],
        stale: true,
        processPanelError: reason,
        _profile: {
            stale: true,
            processPanelError: reason,
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
                .filter(Boolean)
            : [];
        const responsePayload = {
            sessionId: id,
            currentRunId: payload?.currentRunId || null,
            latestSeq: payload?.latestSeq || 0,
            processes,
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
