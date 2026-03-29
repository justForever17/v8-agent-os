import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

function forbidIfAdmin(role?: string | null) {
    return String(role || "").toUpperCase() === "ADMIN";
}

export async function POST() {
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
        const response = await fetch(`${adminApiBaseUrl}/desktop-live/session`, {
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
            { error: error instanceof Error ? error.message : "创建桌面直播会话失败" },
            { status: 500 },
        );
    }
}
