import { NextResponse } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
    try {
        const contextResult = await requireAdminProxyContext();
        if (contextResult.response) {
            return contextResult.response;
        }

        const result = await safeAdminProxyFetch(
            contextResult.context,
            "/models/supervisor-reasoning-effort",
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
