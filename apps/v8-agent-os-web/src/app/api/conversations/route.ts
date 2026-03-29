import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function proxyToAdmin(path: string, method: string, body?: any) {
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
                "x-v8-agent-os-user-email": session.user.email
            },
            body: body ? JSON.stringify(body) : undefined
        });

        // Forward response
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error(`Proxy Error [${method} ${path}]:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET() {
    return proxyToAdmin("/conversations", "GET");
}

export async function POST(req: Request) {
    const body = await req.json().catch(() => ({}));
    return proxyToAdmin("/conversations", "POST", body);
}

export async function DELETE() {
    return proxyToAdmin("/conversations", "DELETE");
}
