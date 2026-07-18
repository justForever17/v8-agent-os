import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

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
    searchParams.set("limit", req.nextUrl.searchParams.get("limit") || "200");
    const before = req.nextUrl.searchParams.get("before");
    if (before) {
        searchParams.set("before", before);
    }

    try {
        const response = await fetch(
            `${ENGINE_URL}/sessions/${encodeURIComponent(id)}/turn-index?${searchParams.toString()}`,
            { method: "GET", headers: { "Content-Type": "application/json" }, cache: "no-store" },
        );
        if (!response.ok) {
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: response.status });
        }
        const payload = asRecord(await response.json().catch(() => ({})));
        return NextResponse.json({
            sessionId: id,
            turns: Array.isArray(payload.turns) ? payload.turns : [],
            pageInfo: asRecord(payload.pageInfo),
        });
    } catch (error) {
        console.error("[Client Conversation Turn Index] Proxy error:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
