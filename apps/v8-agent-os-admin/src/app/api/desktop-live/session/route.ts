import { NextRequest, NextResponse } from "next/server";

import { ensureDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const bridgeBaseUrl = await ensureDesktopLiveBridge();
        const response = await fetch(`${bridgeBaseUrl}/desktop-live/session`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
            body: JSON.stringify({ viewer_id: userEmail }),
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
