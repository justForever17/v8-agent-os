import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }
    const { searchParams } = new URL(req.url);
    const query = new URLSearchParams();
    const sessionId = String(searchParams.get("sessionId") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId is required" }, { status: 400 });
    }
    query.set("sessionId", sessionId);
    query.set("limit", String(searchParams.get("limit") || "100"));
    try {
        const response = await fetch(`${adminApiBaseUrl}/client/sources?${query.toString()}`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Source service unavailable" },
            { status: 502 },
        );
    }
}
