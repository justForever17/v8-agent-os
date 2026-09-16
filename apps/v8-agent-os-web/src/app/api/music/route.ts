import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

export async function GET() {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const res = await fetch(`${clientApiBaseUrl}/music`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, {
            status: res.status,
            headers: { "Cache-Control": "no-store" },
        });
    } catch (error) {
        console.error("Proxy Error [GET /music]:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
