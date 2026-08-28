import { NextRequest, NextResponse } from "next/server";

import {
    proxyEngineJson,
    requireAdminIdentity,
} from "@/lib/server/engine-proxy";

function enginePath(req: NextRequest) {
    const query = req.nextUrl.searchParams.toString();
    return `/audit/logs${query ? `?${query}` : ""}`;
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const { response, data } = await proxyEngineJson(enginePath(req));
    return NextResponse.json(data, { status: response.status });
}

export async function DELETE(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const { response, data } = await proxyEngineJson(enginePath(req), {
        method: "DELETE",
    });
    return NextResponse.json(data, { status: response.status });
}
