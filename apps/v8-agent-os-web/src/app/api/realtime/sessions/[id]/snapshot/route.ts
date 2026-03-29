import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveRouteUserEmail } from "@/lib/route-auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxyToAdmin(path: string, userEmail: string | null) {
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();

    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const res = await fetch(`${adminApiBaseUrl}${path}`, {
            method: "GET",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error(`[Web Snapshot Proxy] ${path} failed:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const session = await auth();
    const userEmail = session?.user?.email || await resolveRouteUserEmail(req);
    const { id } = await params;
    return proxyToAdmin(`/realtime/sessions/${id}/snapshot`, userEmail);
}
