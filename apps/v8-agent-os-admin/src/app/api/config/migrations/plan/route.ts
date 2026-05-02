import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const query = req.nextUrl.searchParams.toString();
    const { response, data } = await proxyEngineJson(`/v1/config/migrations/plan${query ? `?${query}` : ""}`);
    return NextResponse.json(data, { status: response.status });
}
