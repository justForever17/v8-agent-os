import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const payload = await req.json().catch(() => ({}));
    const { response, data } = await proxyEngineJson("/v1/config/migrations/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
    });
    return NextResponse.json(data, { status: response.status });
}
