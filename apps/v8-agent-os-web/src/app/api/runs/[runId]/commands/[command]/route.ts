import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

async function proxyToAdmin(path: string, body: unknown) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();

    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const res = await fetch(`${clientApiBaseUrl}${path}`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: JSON.stringify(body ?? {}),
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error(`[Web Run Command Proxy] ${path} failed:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ runId: string; command: string }> }
) {
    const { runId, command } = await params;
    const body = await req.json().catch(() => ({}));
    return proxyToAdmin(`/runs/${runId}/commands/${command}`, body);
}
