import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine } from "@/lib/server/client-proxy";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const body = await req.json();
        const response = await fetchClientEngine(req, "/memory/session-extraction", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                sessionId: body?.sessionId || body?.session_id,
                userId: userEmail,
            }),
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to start memory extraction" },
            { status: 500 },
        );
    }
}
