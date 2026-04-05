import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson } from "@/lib/server/engine-proxy";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;
    const runId = req.nextUrl.searchParams.get("runId");
    const suffix = runId ? `?run_id=${encodeURIComponent(runId)}` : "";

    try {
        const { response, data } = await proxyEngineJson(`/sessions/${id}/todos${suffix}`);
        return NextResponse.json(data, {
            status: response.status,
            headers: { "Cache-Control": "no-store" },
        });
    } catch (error) {
        console.error(`[Client Sessions] Failed to proxy session todos for ${id}:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
