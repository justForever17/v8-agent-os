import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;
    try {
        const response = await fetch(
            `${resolveEngineBaseUrl()}/sessions/${encodeURIComponent(id)}/workbench/files/read${req.nextUrl.search}`,
            {
                method: "GET",
                headers: {
                    "Content-Type": "application/json",
                    "x-v8-agent-os-user-email": userEmail,
                },
                cache: "no-store",
            },
        );
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        console.error("[Client Workbench File] Engine proxy failed:", error);
        return NextResponse.json({ error: "Engine Service Unavailable" }, { status: 502 });
    }
}
