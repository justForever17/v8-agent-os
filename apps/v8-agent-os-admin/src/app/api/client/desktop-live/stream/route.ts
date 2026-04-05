import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin, forbidIfClientAdmin, requireClientContext } from "@/lib/server/client-proxy";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }
    if (forbidIfClientAdmin(context.user)) {
        return NextResponse.json({ error: "Admin 账号不提供桌面直播观看入口" }, { status: 403 });
    }

    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId 不能为空" }, { status: 400 });
    }

    try {
        const response = await fetchClientAdmin(
            req,
            `/desktop-live/stream?sessionId=${encodeURIComponent(sessionId)}`,
            { method: "GET" },
        );
        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return new NextResponse(detail || "桌面直播流打开失败", { status: response.status || 500 });
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("Content-Type") || "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            },
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "桌面直播流打开失败" },
            { status: error instanceof Error && error.message === "Unauthorized" ? 401 : 502 },
        );
    }
}
