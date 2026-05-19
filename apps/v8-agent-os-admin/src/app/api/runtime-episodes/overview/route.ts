import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const search = req.nextUrl.searchParams.toString();
        const response = await fetch(`${resolveEngineBaseUrl()}/runtime-episodes/overview${search ? `?${search}` : ""}`, {
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        console.error("[Admin Runtime Episodes] overview proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
