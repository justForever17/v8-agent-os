import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const payload = (await req.json().catch(() => ({}))) as {
            sessionId?: string;
            sdp?: string;
            type?: string;
        };
        if (!payload.sessionId || !payload.sdp) {
            return NextResponse.json({ error: "Missing sessionId or sdp" }, { status: 400 });
        }

        const response = await fetch(`${clientApiBaseUrl}/desktop-live/offer`, {
            method: "POST",
            headers: {
                "content-type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: JSON.stringify(payload),
            cache: "no-store",
        });
        const body = await response.json().catch(() => ({}));
        return NextResponse.json(body, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "创建桌面直播 offer 失败" },
            { status: 500 },
        );
    }
}
