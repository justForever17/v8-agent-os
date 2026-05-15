import { NextRequest, NextResponse } from "next/server";

import { ensureDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId 不能为空" }, { status: 400 });
    }

    try {
        const bridgeBaseUrl = await ensureDesktopLiveBridge();
        const upstreamUrl = `${bridgeBaseUrl}/desktop-live/stream?sessionId=${encodeURIComponent(sessionId)}`;
        const response = await fetch(upstreamUrl, {
            cache: "no-store",
            headers: {
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
        });

        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return new NextResponse(detail || "桌面直播流打开失败", { status: response.status || 500 });
        }

        return new NextResponse(response.body, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("Content-Type") || "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "桌面直播流代理失败" },
            { status: 500 },
        );
    }
}
