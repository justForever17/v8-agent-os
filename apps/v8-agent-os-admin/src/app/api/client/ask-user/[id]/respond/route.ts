import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

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
        const answer = typeof payload.answer === "string"
            ? payload.answer
            : typeof payload?.response?.answer === "string"
                ? payload.response.answer
                : "";
        const response = await fetch(`${ENGINE_URL}/ask-user/${encodeURIComponent(id)}/respond`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                response: {
                    ...(payload?.response && typeof payload.response === "object" ? payload.response : {}),
                    answer,
                    approved: true,
                },
            }),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Client AskUser] Respond failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
