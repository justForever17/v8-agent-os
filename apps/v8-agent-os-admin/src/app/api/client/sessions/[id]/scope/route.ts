import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

export async function GET(
    req: NextRequest,
    context: { params: Promise<{ id: string }> },
) {
    const { id } = await context.params;
    return proxyClientEngineJson(req, `/sessions/${encodeURIComponent(id)}/scope`, { method: "GET" });
}

export async function PUT(
    req: NextRequest,
    context: { params: Promise<{ id: string }> },
) {
    const { id } = await context.params;
    const body = await req.text();
    return proxyClientEngineJson(req, `/sessions/${encodeURIComponent(id)}/scope`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body,
    });
}
