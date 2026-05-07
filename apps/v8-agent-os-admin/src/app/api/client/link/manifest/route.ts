import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine, requireClientContext } from "@/lib/server/client-proxy";
import { buildAdminLinkManifest } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }
    try {
        const response = await fetchClientEngine(req, "/link/manifest");
        const payload = await response.json().catch(() => ({}));
        if (response.ok && payload && typeof payload === "object") {
            return NextResponse.json(payload, { status: response.status });
        }
    } catch {
        // Fall back to the Admin-local manifest when the Engine is offline.
    }
    return NextResponse.json(buildAdminLinkManifest(new URL(req.url).origin));
}
