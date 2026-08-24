import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveRouteUserEmail } from "@/lib/route-auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const session = await auth();
    const userEmail = session?.user?.email || await resolveRouteUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();

    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    const { id } = await params;

    try {
        // Web is the rich local workbench surface. Phone/desktop compact
        // snapshots are intentionally bounded, but collapsing the Web stream
        // drops subagent timeline nodes and delays durable artifact refresh.
        const res = await fetch(`${adminApiBaseUrl}/realtime/sessions/${encodeURIComponent(id)}/stream?surface=web`, {
            method: "GET",
            headers: {
                "Content-Type": "text/event-stream",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
            signal: req.signal,
        });

        if (!res.ok || !res.body) {
            const errorText = await res.text().catch(() => "");
            return NextResponse.json({ error: errorText || "Realtime stream unavailable" }, { status: res.status || 502 });
        }

        return new NextResponse(res.body, {
            headers: {
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        });
    } catch (error) {
        console.error("[Web Realtime SSE Proxy] failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
