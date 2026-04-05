import { NextRequest, NextResponse } from "next/server";

import { getDesktopLiveBridgeStatus, scheduleDesktopLiveBridgeIdleStop } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { resolveDesktopLiveBridgeBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const payload = (await req.json().catch(() => ({}))) as { sessionId?: string };
        if (!payload.sessionId) {
            return NextResponse.json({ error: "Missing sessionId" }, { status: 400 });
        }

        const bridgeStatus = await getDesktopLiveBridgeStatus();
        if (!bridgeStatus.bridgeReachable) {
            return NextResponse.json({ success: true, bridgeStopped: false, bridgeReachable: false }, { status: 200 });
        }

        const response = await fetch(`${resolveDesktopLiveBridgeBaseUrl()}/desktop-live/session/${encodeURIComponent(payload.sessionId)}`, {
            method: "DELETE",
            headers: {
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
            cache: "no-store",
        });
        const body = await response.json().catch(() => ({}));
        if (response.ok) {
            scheduleDesktopLiveBridgeIdleStop();
        }
        return NextResponse.json(
            response.ok ? { ...body, bridgeStopped: false } : body,
            { status: response.status },
        );
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "释放桌面直播会话失败" },
            { status: 500 },
        );
    }
}
