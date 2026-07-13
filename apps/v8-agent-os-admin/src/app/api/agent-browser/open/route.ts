import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const payload = await req.json().catch(() => ({}));
    const { response, data } = await proxyEngineJson("/agent-browser/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: String(payload?.url || "about:blank") }),
    });
    return NextResponse.json(data, { status: response.status });
}
