import { NextRequest, NextResponse } from "next/server";

import { proxyEngineResponse, requireAdminIdentity } from "@/lib/server/engine-proxy";

type RouteContext = { params: Promise<{ segments?: string[] }> };

function target(req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((item) => encodeURIComponent(item)).join("/");
    return `/api/plugins${suffix ? `/${suffix}` : ""}${req.nextUrl.search || ""}`;
}

async function forward(req: NextRequest, context: RouteContext, method: string) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    try {
        const params = await context.params;
        const body = method === "GET" || method === "HEAD" ? undefined : await req.text();
        const response = await proxyEngineResponse(target(req, params.segments), {
            method,
            headers: body ? { "Content-Type": req.headers.get("content-type") || "application/json" } : undefined,
            body,
        });
        const headers = new Headers();
        const contentType = response.headers.get("content-type");
        if (contentType) headers.set("Content-Type", contentType);
        const cacheControl = response.headers.get("cache-control");
        if (cacheControl) headers.set("Cache-Control", cacheControl);
        return new NextResponse(response.body, { status: response.status, headers });
    } catch (error) {
        console.error(`[Plugin Manager] Failed to proxy ${method}:`, error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export const GET = (req: NextRequest, context: RouteContext) => forward(req, context, "GET");
export const POST = (req: NextRequest, context: RouteContext) => forward(req, context, "POST");
export const DELETE = (req: NextRequest, context: RouteContext) => forward(req, context, "DELETE");
