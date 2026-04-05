import { NextRequest, NextResponse } from "next/server";

import { warmDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const payload = await warmDesktopLiveBridge();
        return NextResponse.json(payload, { status: 200 });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "预热桌面直播 bridge 失败" },
            { status: 500 },
        );
    }
}
