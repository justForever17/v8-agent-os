import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { data: health } = await proxyEngineJson("/health");

        return NextResponse.json({
            health: health || {},
        });
    } catch (error) {
        console.error("[Operations Center] Failed to load summary:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
