import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
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
        console.error(`[Admin] Failed to proxy session todos for ${id}:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
