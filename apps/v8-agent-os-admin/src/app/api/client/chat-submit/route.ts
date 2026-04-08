import { NextRequest, NextResponse } from "next/server";

import { buildEngineChatRequestPayload } from "@/lib/realtime/engine-chat-request";
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
        const payload = await req.json();
        const context = buildEngineChatRequestPayload(payload, userEmail);
        const response = await fetchClientEngine(req, "/chat/submit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(context.pythonPayload),
        });

        const json = await response.json().catch(() => ({}));
        return NextResponse.json(json, { status: response.status });
    } catch (error: unknown) {
        console.error("[ClientChatSubmitAPI] Fatal Error:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
