import { NextRequest, NextResponse } from "next/server";

import { getDesktopLiveBridgeStatus } from "@/lib/server/desktop-live-bridge";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const payload = await getDesktopLiveBridgeStatus();
        return NextResponse.json(payload, { status: 200 });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "读取 Desktop Live 状态失败" },
            { status: 500 },
        );
    }
}
