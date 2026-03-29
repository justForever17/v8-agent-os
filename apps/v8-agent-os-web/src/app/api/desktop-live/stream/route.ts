import { NextRequest, NextResponse } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayStreamProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";

function forbidIfAdmin(role?: string | null) {
    return String(role || "").toUpperCase() === "ADMIN";
}

export async function GET(req: NextRequest) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }
    if (forbidIfAdmin(contextResult.context.userRole)) {
        return NextResponse.json({ error: "Admin 账号不提供桌面直播观看入口" }, { status: 403 });
    }

    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId 不能为空" }, { status: 400 });
    }

    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/desktop-live/stream?sessionId=${encodeURIComponent(sessionId)}`,
        { method: "GET" },
        "/desktop-live/stream",
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    const response = result.response;
    if (!response.ok || !response.body) {
        const detail = await response.text().catch(() => "");
        return new NextResponse(detail || "桌面直播流打开失败", { status: response.status || 500 });
    }

    return relayStreamProxyResponse(response, {
        cacheControl: "no-store, no-cache, must-revalidate, max-age=0",
        defaultContentType: "multipart/x-mixed-replace; boundary=frame",
    });
}
