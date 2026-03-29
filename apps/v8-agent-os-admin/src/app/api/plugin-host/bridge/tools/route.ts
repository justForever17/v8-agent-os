import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const search = req.nextUrl.searchParams;
    const query = search.get("query");
    const limit = search.get("limit");
    const refresh = search.get("refresh");
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    if (limit) params.set("limit", limit);
    if (refresh) params.set("refresh", refresh);
    const suffix = params.toString() ? `/plugin-host/bridge/tools?${params.toString()}` : "/plugin-host/bridge/tools";

    try {
        const { response, data } = await proxyEngineJson(suffix);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to proxy plugin-host bridge tools:", error);
        return NextResponse.json({ error: "Failed to load bridge tools" }, { status: 500 });
    }
}
