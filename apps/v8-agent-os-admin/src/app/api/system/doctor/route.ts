import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const { response, data } = await proxyEngineJson("/v1/system/doctor");
    return NextResponse.json(data, { status: response.status });
}
