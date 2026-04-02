import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getRuntimeInstallState, triggerDesktopInstall } from "@/lib/server/runtime-install";
import { verifyServiceAuth } from "@/lib/service-auth";

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }
    return userEmail;
}

export async function GET(req: NextRequest) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        return NextResponse.json(await getRuntimeInstallState());
    } catch (error) {
        console.error("[Admin Runtime Install] Failed to read state:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const payload = await req.json().catch(() => ({}));
        const result = await triggerDesktopInstall(payload?.platform);
        return NextResponse.json({ status: "started", ...result });
    } catch (error) {
        console.error("[Admin Runtime Install] Failed to start desktop install:", error);
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to start desktop install" },
            { status: 500 },
        );
    }
}
