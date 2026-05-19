import { NextRequest, NextResponse } from "next/server";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const startedAt = Date.now();
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    const { id } = await params;
    const afterSeq = req.nextUrl.searchParams.get("after_seq");
    const search = afterSeq ? `?after_seq=${encodeURIComponent(afterSeq)}` : "";

    try {
        const res = await fetch(`${ENGINE_URL}/sessions/${id}/runtime-events${search}`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        const elapsedMs = Date.now() - startedAt;
        const payloadBytes = jsonSizeBytes(data);
        recordAdminApiMetric({
            route: "admin.realtime.events",
            status: res.status,
            elapsedMs,
            payloadBytes,
            engineElapsedMs: readEngineElapsedMs(data),
        });
        return NextResponse.json(data, {
            status: res.status,
            headers: {
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(payloadBytes),
            },
        });
    } catch (error) {
        console.error("[Admin Realtime Events] Engine proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
