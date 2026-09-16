import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

async function releaseDesktopLiveSession(_req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const { id } = await context.params;
        const response = await fetch(`${clientApiBaseUrl}/desktop-live/session/${encodeURIComponent(id)}`, {
            method: "DELETE",
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
            { error: error instanceof Error ? error.message : "释放桌面直播会话失败" },
            { status: 500 },
        );
    }
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    return releaseDesktopLiveSession(req, context);
}

export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    return releaseDesktopLiveSession(req, context);
}
