import { NextRequest, NextResponse } from "next/server";

import { requireClientContext } from "@/lib/server/client-proxy";
import { buildClientLinkManifest, resolveRequestOrigin } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }
    return NextResponse.json(buildClientLinkManifest(resolveRequestOrigin(req)));
}
