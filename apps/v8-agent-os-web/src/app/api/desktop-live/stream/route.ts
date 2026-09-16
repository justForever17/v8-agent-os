import { NextRequest, NextResponse } from "next/server";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayStreamProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }
    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    if (!sessionId) {
        return NextResponse.json({ error: "sessionId 不能为空" }, { status: 400 });
    }

    const result = await safeClientProxyFetch(
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
