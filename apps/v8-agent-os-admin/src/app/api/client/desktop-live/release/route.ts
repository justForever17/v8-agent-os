import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin, forbidIfClientAdmin, requireClientContext } from "@/lib/server/client-proxy";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }
    if (forbidIfClientAdmin(context.user)) {
        return NextResponse.json({ error: "Admin 账号不提供桌面直播观看入口" }, { status: 403 });
    }
    try {
        const body = await req.text();
        const response = await fetchClientAdmin(req, "/desktop-live/release", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "释放桌面直播会话失败" },
            { status: error instanceof Error && error.message === "Unauthorized" ? 401 : 502 },
        );
    }
}
