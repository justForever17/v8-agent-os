import { NextRequest, NextResponse } from "next/server";

import { revokeMobileRefreshToken } from "@/lib/mobile-auth";

export async function POST(req: NextRequest) {
    try {
        const payload = await req.json().catch(() => ({}));
        const refreshToken = String(payload?.refreshToken || "").trim();
        if (!refreshToken) {
            return NextResponse.json({ error: "Refresh token is required" }, { status: 400 });
        }
        revokeMobileRefreshToken(refreshToken);
        return NextResponse.json({ success: true });
    } catch (error) {
        console.error("[MobileAuth] logout failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
