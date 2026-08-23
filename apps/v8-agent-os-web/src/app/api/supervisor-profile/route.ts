import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export const dynamic = "force-dynamic";

export async function GET() {
    const session = await auth();
    const userEmail = session?.user?.email;
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }
    try {
        const response = await fetch(`${adminApiBaseUrl}/client/supervisor-profile`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, {
            status: response.status,
            headers: { "Cache-Control": "no-store" },
        });
    } catch {
        return NextResponse.json({ error: "Supervisor profile unavailable" }, { status: 502 });
    }
}
