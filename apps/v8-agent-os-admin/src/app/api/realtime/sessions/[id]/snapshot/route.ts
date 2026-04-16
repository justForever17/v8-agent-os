import { NextRequest, NextResponse } from "next/server";
import {
    resolveClientSurfaceOriginFromRequest,
    resolveEngineBaseUrl,
} from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { normalizeSnapshotForRealtimeSurface } from "@/lib/server/session-realtime-resource";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    const { id } = await params;
    const publicBaseUrl = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: true });

    try {
        const res = await fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        const data = normalizeSnapshotForRealtimeSurface(
            await res.json().catch(() => ({})),
            { publicBaseUrl },
        );
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Admin Realtime Snapshot] Engine proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
