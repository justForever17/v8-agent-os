import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

function forbidIfAdmin(role?: string | null) {
    return String(role || "").toUpperCase() === "ADMIN";
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    if (forbidIfAdmin(session.user.role)) {
        return NextResponse.json({ error: "Admin 账号不提供桌面直播观看入口" }, { status: 403 });
    }

    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const payload = (await req.json().catch(() => ({}))) as {
            sessionId?: string;
            candidate?: Record<string, unknown> | null;
        };
        if (!payload.sessionId) {
            return NextResponse.json({ error: "Missing sessionId" }, { status: 400 });
        }

        const response = await fetch(`${adminApiBaseUrl}/desktop-live/candidate`, {
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
            { error: error instanceof Error ? error.message : "提交桌面直播 candidate 失败" },
            { status: 500 },
        );
    }
}
