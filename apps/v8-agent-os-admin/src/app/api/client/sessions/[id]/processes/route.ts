import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { normalizeProcessForRealtimeSurface } from "@/lib/server/session-realtime-resource";

const ENGINE_URL = resolveEngineBaseUrl();

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

    try {
        const response = await fetch(`${ENGINE_URL}/sessions/${id}/processes`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!response.ok) {
            console.error("[Client Session Processes] Failed to fetch engine process surface:", await response.text());
            return NextResponse.json({ error: "Failed to fetch process surface" }, { status: 500 });
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
        console.error("[Client Session Processes] Engine communication failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
