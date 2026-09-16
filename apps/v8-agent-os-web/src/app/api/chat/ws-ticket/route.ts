import { NextResponse } from "next/server";

export const runtime = "nodejs";

export async function GET() {
    return NextResponse.json(
        {
            error: "浏览器直连 Engine WebSocket 已退役，请使用 Web 本机入口的 Engine HTTP 流。",
            deprecated: true,
        },
        { status: 410 },
    );
}
