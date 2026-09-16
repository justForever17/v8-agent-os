import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function POST() {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const response = await fetch(`${clientApiBaseUrl}/desktop-live/prepare`, {
            method: "POST",
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
            { error: error instanceof Error ? error.message : "预热桌面直播失败" },
            { status: 500 },
        );
    }
}
