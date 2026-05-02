import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ runId: string }> }
) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const { runId } = await params;
    const { response, data } = await proxyEngineJson(`/v1/runs/${encodeURIComponent(runId)}/ledger`);
    return NextResponse.json(data, { status: response.status });
}
