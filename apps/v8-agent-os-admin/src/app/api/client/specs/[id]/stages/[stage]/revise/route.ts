import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string; stage: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id, stage } = await params;
    try {
        const payload = await req.json().catch(() => ({}));
        const response = await fetch(`${ENGINE_URL}/specs/${encodeURIComponent(id)}/stages/${encodeURIComponent(stage)}/revise`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, userEmail }),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Client Specs] Revise failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
