import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

async function proxyToAdmin(path: string, method: string, body?: unknown) {
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
            method,
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error(`Proxy Error [${method} ${path}]:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const { id } = await params;
    return proxyToAdmin(`/sessions/${id}/scope`, "GET");
}

export async function PUT(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const { id } = await params;
    const body = await req.json();
    return proxyToAdmin(`/sessions/${id}/scope`, "PUT", body);
}
