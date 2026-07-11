import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) return unauthorizedClientJson();
    try {
        const response = await fetch(`${ENGINE_ORIGIN}/v1/api/plugins/mentions`, { cache: "no-store" });
        const data = await response.json();
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Client Plugins] Error fetching plugin mentions:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
