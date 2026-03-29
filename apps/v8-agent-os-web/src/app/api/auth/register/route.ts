import { NextRequest, NextResponse } from "next/server";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const body = await req.json();
        const res = await fetch(`${adminApiBaseUrl}/auth/register`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret
            },
            body: JSON.stringify(body)
        });

        const data = await res.json();
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("Register Proxy Error:", error);
        return NextResponse.json({ error: "Service Unavailable" }, { status: 502 });
    }
}
