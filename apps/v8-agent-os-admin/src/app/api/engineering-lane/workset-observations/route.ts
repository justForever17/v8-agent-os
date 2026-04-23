import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

function targetUrl(req: NextRequest) {
    const search = req.nextUrl.searchParams.toString();
    return `${resolveEngineBaseUrl()}/engineering-lane/workset-observations${search ? `?${search}` : ""}`;
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }
    try {
        const response = await fetch(targetUrl(req), { method: "GET", cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Engineering Workset Observations] list failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
