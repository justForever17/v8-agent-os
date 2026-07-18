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
    const searchParams = new URLSearchParams();
    searchParams.set("limit", req.nextUrl.searchParams.get("limit") || "1");
    const before = req.nextUrl.searchParams.get("before");
    if (before) {
        searchParams.set("before", before);
    }
    const around = req.nextUrl.searchParams.get("around");
    if (around) {
        searchParams.set("around", around);
    }
    const radius = req.nextUrl.searchParams.get("radius");
    if (radius) {
        searchParams.set("radius", radius);
    }
    const publicBaseUrl = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: false });
    const compactSurface = req.nextUrl.searchParams.get("compact") === "1"
        || req.nextUrl.searchParams.get("surface") === "phone"
        || req.nextUrl.searchParams.get("surface") === "web";

    try {
        const turnsResponse = await fetch(`${ENGINE_URL}/sessions/${encodeURIComponent(id)}/turns?${searchParams.toString()}`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!turnsResponse.ok) {
            console.error("[Client Conversation Turns] Failed to fetch turn page:", await turnsResponse.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: turnsResponse.status });
        }

        const data = asRecord(await turnsResponse.json().catch(() => ({})));
        const rawMessages = Array.isArray(data.messages) ? data.messages : [];
        const messages = rawMessages.map((message: unknown) => normalizeMessageForRealtimeSurface(message, { publicBaseUrl, compactSurface }));

        return NextResponse.json({
            sessionId: id,
            syncCursor: typeof data.syncCursor === "string" ? data.syncCursor : null,
            messages,
            pageInfo: asRecord(data.pageInfo),
        });
    } catch (error) {
        console.error("[Client Conversation Turns] Proxy error:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
