import { NextRequest, NextResponse } from "next/server";

import { getDesktopLiveBridgeStatus } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const payload = await getDesktopLiveBridgeStatus();
        return NextResponse.json(payload, { status: 200 });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "读取桌面直播状态失败" },
            { status: 500 },
        );
    }
}
