import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import { getV8OSUpdateState } from "@/lib/server/v8os-update";

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

    const state = await getV8OSUpdateState({
        force: req.nextUrl.searchParams.get("refresh") === "1",
    });
    return NextResponse.json(state, {
        headers: { "Cache-Control": "private, no-store, max-age=0" },
    });
}
