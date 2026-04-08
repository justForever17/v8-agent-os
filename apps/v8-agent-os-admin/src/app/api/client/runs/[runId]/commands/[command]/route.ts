import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

export async function POST(
    req: NextRequest,
    context: { params: Promise<{ runId: string; command: string }> },
) {
    const { runId, command } = await context.params;
    const body = await req.text();
    return proxyClientEngineJson(
        req,
        `/runs/${encodeURIComponent(runId)}/commands/${encodeURIComponent(command)}`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        },
    );
}
