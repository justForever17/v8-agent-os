import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/models/defaults");
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;

        const payload = await req.json();
        const { response, data } = await proxyEngineJson("/models/defaults", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
