import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getRuntimeFeaturePackState, triggerFeaturePackInstall } from "@/lib/server/runtime-feature-packs";
import { verifyServiceAuth } from "@/lib/service-auth";
import { LOCALE_COOKIE_NAME } from "@/lib/locale";

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
        return NextResponse.json(await getRuntimeFeaturePackState());
    } catch (error) {
        console.error("[Admin Runtime Feature Packs] Failed to read state:", error);
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
        const result = await triggerFeaturePackInstall(
            String(payload?.packId || ""),
            Boolean(payload?.dryRun),
            String(payload?.locale || req.cookies.get(LOCALE_COOKIE_NAME)?.value || "en"),
        );
        return NextResponse.json(result);
    } catch (error) {
        console.error("[Admin Runtime Feature Packs] Failed to start install:", error);
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to start feature pack install" },
            { status: 500 },
        );
    }
}
