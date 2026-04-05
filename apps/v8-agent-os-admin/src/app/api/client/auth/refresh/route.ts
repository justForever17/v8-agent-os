import { NextRequest, NextResponse } from "next/server";

import { rotateMobileSession } from "@/lib/mobile-auth";

export async function POST(req: NextRequest) {
    try {
        const payload = await req.json().catch(() => ({}));
        const refreshToken = String(payload?.refreshToken || "").trim();
        const deviceName = String(payload?.deviceName || "").trim();

        if (!refreshToken) {
            return NextResponse.json({ error: "Refresh token is required" }, { status: 400 });
        }

        const session = await rotateMobileSession(refreshToken, deviceName);
        if (!session) {
            return NextResponse.json({ error: "Invalid refresh token" }, { status: 401 });
        }

        return NextResponse.json(session);
    } catch (error) {
        console.error("[ClientAuth] refresh failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
