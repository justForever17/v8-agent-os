import { NextRequest, NextResponse } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/workspace/resource${req.nextUrl.search}`,
        {
            method: "GET",
            headers: req.headers.get("range")
                ? { Range: String(req.headers.get("range")) }
                : undefined,
        },
        "/workspace/resource",
    );
    if (result.errorResponse) {
        return result.errorResponse;
    }

    const response = result.response;
    const body = response.body || await response.arrayBuffer();
    const headers = new Headers();
    for (const name of [
        "Content-Type",
        "Content-Length",
        "Content-Disposition",
        "Accept-Ranges",
        "Content-Range",
        "Cache-Control",
        "X-V8-Workspace-Relative-Path",
        "X-V8-Path-Plane",
        "X-V8-Workspace-Id",
        "X-V8-Project-Id",
    ]) {
        const value = response.headers.get(name);
        if (value) {
            headers.set(name, value);
        }
    }

    return new NextResponse(body, {
        status: response.status,
        headers,
    });
}
