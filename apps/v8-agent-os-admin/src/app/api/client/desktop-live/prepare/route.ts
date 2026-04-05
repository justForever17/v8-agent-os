import { NextRequest, NextResponse } from "next/server";

import { warmDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const payload = await warmDesktopLiveBridge();
        return NextResponse.json(payload, { status: 200 });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "预热 Desktop Live bridge 失败" },
            { status: 500 },
        );
    }
}
