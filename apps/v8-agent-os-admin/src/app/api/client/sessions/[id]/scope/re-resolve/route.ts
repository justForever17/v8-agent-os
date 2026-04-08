import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

export async function POST(
    req: NextRequest,
    context: { params: Promise<{ id: string }> },
) {
    const { id } = await context.params;
    const body = await req.text();
    return proxyClientEngineJson(req, `/sessions/${encodeURIComponent(id)}/scope/re-resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
    });
}
