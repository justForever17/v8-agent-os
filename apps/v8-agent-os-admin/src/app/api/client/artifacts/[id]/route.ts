import { NextRequest, NextResponse } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const { id } = await context.params;
    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId is required" }, { status: 400 });
    }
    const query = new URLSearchParams({ sessionId });
    return proxyClientAdminJson(req, `/memory/artifacts/${encodeURIComponent(id)}?${query.toString()}`, { method: "GET" });
}
