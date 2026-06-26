import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveClientSurfaceOriginFromRequest, resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { normalizeMessageForRealtimeSurface } from "@/lib/server/session-realtime-resource";

const ENGINE_URL = resolveEngineBaseUrl();

function asRecord(value: unknown) {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;
    const since = req.nextUrl.searchParams.get("since") || "";
    const publicBaseUrl = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: false });

    try {
        const syncResponse = await fetch(`${ENGINE_URL}/sessions/${id}/timeline/sync?since=${encodeURIComponent(since)}`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!syncResponse.ok) {
            console.error("[Client Conversations Sync] Failed to fetch sync data:", await syncResponse.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: syncResponse.status });
        }

        const syncData = asRecord(await syncResponse.json().catch(() => ({})));
        const rawMessages = Array.isArray(syncData.messages) ? syncData.messages : [];
        const messages = rawMessages.map((message: unknown) => normalizeMessageForRealtimeSurface(message, { publicBaseUrl }));
            
        // We do not apply canonical source group here because these are sparse updates.
        // The mobile client doesn't heavily depend on the proxy grouping anyway, it just renders flat or handles groups itself.
        
        return NextResponse.json({
            messages,
            deletions: Array.isArray(syncData.deletions) ? syncData.deletions : [],
            syncCursor: syncData.syncCursor,
            sessionId: id,
        });

    } catch (error) {
        console.error("[Client Conversations Sync] Proxy error:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
