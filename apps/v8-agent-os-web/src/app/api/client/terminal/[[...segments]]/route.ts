import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

async function proxy(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }, method: "GET" | "POST") {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const { segments } = await context.params;
        const suffix = (segments || []).map((item) => encodeURIComponent(item)).join("/");
        const search = req.nextUrl.searchParams.toString();
        const target = `${adminApiBaseUrl}/terminal${suffix ? `/${suffix}` : ""}${search ? `?${search}` : ""}`;
        const init: RequestInit = {
            method,
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        };
        if (method === "POST") {
            const payload = await req.json().catch(() => ({}));
            init.body = JSON.stringify(payload);
        }
        const res = await fetch(target, init);
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Web Terminal Proxy] failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "GET");
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "POST");
}
