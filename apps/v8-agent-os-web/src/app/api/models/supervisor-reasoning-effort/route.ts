import { NextRequest, NextResponse } from "next/server";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function sessionQuery(req: NextRequest) {
    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    return sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : "";
}

export async function GET(req: NextRequest) {
    try {
        const contextResult = await requireClientProxyContext();
        if (contextResult.response) {
            return contextResult.response;
        }

        const result = await safeClientProxyFetch(
            contextResult.context,
            `/models/supervisor-reasoning-effort${sessionQuery(req)}`,
            { method: "GET" },
            "/models/supervisor-reasoning-effort",
        );
        if (result.errorResponse) {
            return result.errorResponse;
        }

        const payload = await result.response.json().catch(() => ({}));
        if (result.response.status === 404) {
            return NextResponse.json({ visible: false, levels: [] });
        }
        return NextResponse.json(payload, { status: result.response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function PATCH(req: NextRequest) {
    try {
        const contextResult = await requireClientProxyContext();
        if (contextResult.response) return contextResult.response;
        const body = await req.text();
        const result = await safeClientProxyFetch(
            contextResult.context,
            "/models/supervisor-reasoning-effort",
            {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body,
            },
            "/models/supervisor-reasoning-effort",
        );
        if (result.errorResponse) return result.errorResponse;
        const payload = await result.response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: result.response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
