import { NextRequest, NextResponse } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    const { searchParams } = new URL(req.url);
    const sessionId = String(searchParams.get("sessionId") || searchParams.get("session_id") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId is required" }, { status: 400 });
    }
    const limit = String(searchParams.get("limit") || "100").trim();
    const query = new URLSearchParams({ session_id: sessionId, limit });
    return proxyClientEngineJson(req, `/sources?${query.toString()}`, {
        method: "GET",
        cache: "no-store",
    });
}
