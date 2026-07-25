import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveRouteUserEmail } from "@/lib/route-auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    const session = await auth();
    const userEmail = session?.user?.email || await resolveRouteUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const response = await fetch(`${adminApiBaseUrl}/client/realtime/session-activity/stream`, {
            method: "GET",
            headers: {
                Accept: "text/event-stream",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
            signal: req.signal,
        });
        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return NextResponse.json(
                { error: detail || "Realtime stream unavailable" },
                { status: response.status || 502 },
            );
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: {
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                Connection: "keep-alive",
            },
        });
    } catch (error) {
        console.error("[Web Session Activity SSE] failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
