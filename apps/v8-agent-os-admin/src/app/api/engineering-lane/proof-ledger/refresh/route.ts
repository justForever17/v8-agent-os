import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }
    try {
        const payload = await req.json().catch(() => ({}));
        const response = await fetch(`${resolveEngineBaseUrl()}/engineering-lane/proof-ledger/refresh`, {
            method: "POST",
            cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Engineering Proof Ledger] refresh failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
