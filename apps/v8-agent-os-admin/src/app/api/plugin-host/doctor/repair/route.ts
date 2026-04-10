import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    try {
        const { response, data } = await proxyEngineJson("/plugin-host/doctor/repair", {
            method: "POST",
        });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to proxy plugin-host doctor repair:", error);
        return NextResponse.json({ error: "Failed to repair plugin-host doctor issues" }, { status: 500 });
    }
}
