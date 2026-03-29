import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type RouteContext = {
    params: Promise<{ domain: string }>;
};

export async function GET(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    const { domain } = await context.params;

    try {
        const { response, data } = await proxyEngineJson(`/config-registry/${encodeURIComponent(domain)}`);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Admin Config Registry] Failed to load domain:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function POST(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    const { domain } = await context.params;

    try {
        const payload = await req.json().catch(() => ({}));
        const { response, data } = await proxyEngineJson(`/config-registry/${encodeURIComponent(domain)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Admin Config Registry] Failed to save domain:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
