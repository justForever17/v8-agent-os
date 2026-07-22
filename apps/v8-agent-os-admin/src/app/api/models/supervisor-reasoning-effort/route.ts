import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;

        const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
        const suffix = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : "";
        const { response, data } = await proxyEngineJson(`/models/supervisor-reasoning-effort${suffix}`);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function PATCH(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/models/supervisor-reasoning-effort", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: await req.text(),
        });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
