import { NextRequest, NextResponse } from "next/server";

import { getDesktopLiveBridgeStatus, stopDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { resolveDesktopLiveBridgeBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const { id } = await context.params;
        const bridgeStatus = await getDesktopLiveBridgeStatus();
        if (!bridgeStatus.bridgeReachable) {
            await stopDesktopLiveBridge();
            return NextResponse.json({ success: true, bridgeStopped: true }, { status: 200 });
        }

        const response = await fetch(`${resolveDesktopLiveBridgeBaseUrl()}/desktop-live/session/${encodeURIComponent(id)}`, {
            method: "DELETE",
            headers: {
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        if (response.ok) {
            await stopDesktopLiveBridge();
        }
        return NextResponse.json(
            response.ok ? { ...payload, bridgeStopped: true } : payload,
            { status: response.status },
        );
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "释放桌面直播会话失败" },
            { status: 500 },
        );
    }
}
