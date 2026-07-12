import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

async function proxyToAdmin(method: "GET" | "PUT", body?: unknown) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }
    try {
        const response = await fetch(`${adminApiBaseUrl}/workspace-presentations`, {
            method,
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        return NextResponse.json(await response.json().catch(() => ({})), { status: response.status });
    } catch (error) {
        console.error(`[Workspace Presentations] ${method} failed:`, error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET() {
    return proxyToAdmin("GET");
}

export async function PUT(req: Request) {
    return proxyToAdmin("PUT", await req.json().catch(() => ({})));
}
