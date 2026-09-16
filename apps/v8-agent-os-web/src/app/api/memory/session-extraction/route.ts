import { NextRequest, NextResponse } from "next/server";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
    try {
        const contextResult = await requireClientProxyContext();
        if (contextResult.response) {
            return contextResult.response;
        }

        const body = await req.json();
        const result = await safeClientProxyFetch(
            contextResult.context,
            "/client/memory/session-extraction",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sessionId: body?.sessionId || body?.session_id }),
            },
            "/client/memory/session-extraction",
        );
        if (result.errorResponse) {
            return result.errorResponse;
        }
        const payload = await result.response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: result.response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to start memory extraction" },
            { status: 500 },
        );
    }
}
