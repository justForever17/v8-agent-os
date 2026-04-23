import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest, context: { params: Promise<{ entryId: string }> }) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }
    try {
        const { entryId } = await context.params;
        const response = await fetch(`${resolveEngineBaseUrl()}/engineering-lane/proof-ledger/${encodeURIComponent(entryId)}`, {
            method: "GET",
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Engineering Proof Ledger] detail failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
