import { NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET() {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/agents/tool-surface");
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        return NextResponse.json(data);
    } catch (error) {
        console.error("Failed to read agent tool surface:", error);
        return NextResponse.json({ error: "Failed to read agent tool surface" }, { status: 500 });
    }
}
