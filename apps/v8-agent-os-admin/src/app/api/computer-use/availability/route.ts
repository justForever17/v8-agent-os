import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const refreshEnvironment = ["1", "true"].includes(String(req.nextUrl.searchParams.get("refresh") || "").toLowerCase());
    const { response, data } = await proxyEngineJson(`/computer-use/availability${refreshEnvironment ? "?refresh=true" : ""}`);
    return NextResponse.json(data, { status: response.status });
}
