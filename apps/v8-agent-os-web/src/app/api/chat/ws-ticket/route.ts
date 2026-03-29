import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function GET() {
    return NextResponse.json(
        {
            error: "浏览器直连 Engine WebSocket 已退役，请改用 Admin 单桥接 HTTP 流。",
            deprecated: true,
        },
        { status: 410 },
    );
}
