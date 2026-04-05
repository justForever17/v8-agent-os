import { NextRequest, NextResponse } from "next/server";

import { mobileAuthUserResponse, resolveMobileAccessUser } from "@/lib/mobile-auth";

export async function GET(req: NextRequest) {
    try {
        const user = await resolveMobileAccessUser(req);
        if (!user) {
            return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
        }
        return NextResponse.json({ user: mobileAuthUserResponse(user) });
    } catch (error) {
        console.error("[ClientAuth] me failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
