import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const refresh = req.nextUrl.searchParams.get("refresh");
    const suffix = refresh ? `/plugin-host/doctor?refresh=${encodeURIComponent(refresh)}` : "/plugin-host/doctor";

    try {
        const { response, data } = await proxyEngineJson(suffix);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to proxy plugin-host doctor:", error);
        return NextResponse.json({ error: "Failed to run plugin-host doctor" }, { status: 500 });
    }
}
