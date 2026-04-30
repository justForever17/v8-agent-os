import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const { id } = await params;
    const body = await req.text();
    const { response, data } = await proxyEngineJson(`/v1/observability/tool-observations/${encodeURIComponent(id)}/reveal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body || "{}",
    });
    return NextResponse.json(data, { status: response.status });
}
