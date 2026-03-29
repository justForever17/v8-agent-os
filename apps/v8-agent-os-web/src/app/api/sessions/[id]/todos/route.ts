import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

async function proxyToAdmin(path: string) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const res = await fetch(`${adminApiBaseUrl}${path}`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status, headers: { "Cache-Control": "no-store" } });
    } catch (error) {
        console.error(`Proxy Error [GET ${path}]:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const { id } = await params;
    const runId = req.nextUrl.searchParams.get("runId");
    const suffix = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return proxyToAdmin(`/sessions/${id}/todos${suffix}`);
}
