import { NextRequest, NextResponse } from "next/server";

import { createMobileSession } from "@/lib/mobile-auth";

export async function POST(req: NextRequest) {
    try {
        const payload = await req.json().catch(() => ({}));
        const login = String(payload?.login || payload?.email || "").trim();
        const password = String(payload?.password || "");
        const deviceName = String(payload?.deviceName || "").trim();

        if (!login || !password) {
            return NextResponse.json({ error: "Missing credentials" }, { status: 400 });
        }

        const session = await createMobileSession(login, password, deviceName);
        if (!session) {
            return NextResponse.json({ error: "登录名或密码错误" }, { status: 401 });
        }

        return NextResponse.json(session);
    } catch (error) {
        console.error("[MobileAuth] login failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
