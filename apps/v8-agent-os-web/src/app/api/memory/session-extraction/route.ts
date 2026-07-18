import { NextRequest, NextResponse } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
    try {
        const contextResult = await requireAdminProxyContext();
        if (contextResult.response) {
            return contextResult.response;
        }

        const body = await req.json();
        const result = await safeAdminProxyFetch(
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
