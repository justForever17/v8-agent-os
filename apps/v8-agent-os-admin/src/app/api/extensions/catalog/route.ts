import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { response, data } = await proxyEngineJson("/extensions/catalog");
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Extensions Catalog] Failed to load catalog:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
