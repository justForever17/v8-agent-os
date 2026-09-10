import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;

    try {
        const payload = await req.json().catch(() => ({}));
        const response = await fetch(`${ENGINE_URL}/approvals/${id}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "x-v8-agent-os-user-email": userEmail, "x-v8-agent-os-secret": resolveInternalSecret() },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Client Approvals] Approve failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
