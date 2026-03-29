import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { searchParams } = new URL(req.url);
        const windowHours = Number(searchParams.get("windowHours") || "24");
        const safeWindowHours = Number.isFinite(windowHours) ? Math.max(1, Math.min(windowHours, 168)) : 24;
        const { response, data } = await proxyEngineJson(`/extensions/usage-summary?window_hours=${safeWindowHours}`);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Extensions Usage Summary] Failed to load summary:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
