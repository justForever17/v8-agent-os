import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const { id } = await context.params;
    const { response, data } = await proxyEngineJson(`/v1/config/migrations/rollback/${encodeURIComponent(id)}`, {
        method: "POST",
    });
    return NextResponse.json(data, { status: response.status });
}
