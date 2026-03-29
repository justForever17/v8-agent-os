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
        const payload = (await req.json().catch(() => ({}))) as {
            sessionId?: string;
            sdp?: string;
            type?: string;
        };
        if (!payload.sessionId || !payload.sdp) {
            return NextResponse.json({ error: "Missing sessionId or sdp" }, { status: 400 });
        }

        const bridgeBaseUrl = await ensureDesktopLiveBridge();
        const response = await fetch(`${bridgeBaseUrl}/desktop-live/offer`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
            body: JSON.stringify({
                session_id: payload.sessionId,
                viewer_id: userEmail,
                sdp: payload.sdp,
                type: payload.type || "offer",
            }),
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
