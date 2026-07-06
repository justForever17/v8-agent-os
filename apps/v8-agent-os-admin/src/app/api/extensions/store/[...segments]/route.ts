import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type RouteContext = {
    params: Promise<{ segments?: string[] }>;
};

function buildTarget(req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((segment) => encodeURIComponent(segment)).join("/");
    return `/extensions/store${suffix ? `/${suffix}` : ""}${req.nextUrl.search || ""}`;
}

export async function GET(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const params = await context.params;
        const { response, data } = await proxyEngineJson(buildTarget(req, params.segments));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Extensions Store] Failed to proxy GET:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function POST(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const params = await context.params;
        const body = await req.text();
        const { response, data } = await proxyEngineJson(buildTarget(req, params.segments), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Extensions Store] Failed to proxy POST:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
