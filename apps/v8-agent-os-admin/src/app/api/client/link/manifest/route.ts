import { NextRequest, NextResponse } from "next/server";

import { requireClientContext } from "@/lib/server/client-proxy";
import { buildAdminLinkManifest } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }
    return NextResponse.json(buildAdminLinkManifest(new URL(req.url).origin));
}
