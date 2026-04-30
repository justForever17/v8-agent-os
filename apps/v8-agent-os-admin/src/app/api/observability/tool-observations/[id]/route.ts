import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const { id } = await params;
    const { response, data } = await proxyEngineJson(`/v1/observability/tool-observations/${encodeURIComponent(id)}`);
    return NextResponse.json(data, { status: response.status });
}
