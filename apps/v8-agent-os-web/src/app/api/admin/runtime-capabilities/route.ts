import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";

import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";
import { verifyServiceAuth } from "@admin/lib/service-auth";

const ENGINE_URL = resolveEngineBaseUrl();

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.adminAuthenticated && session.user.role === "ADMIN" ? session.user.email : null;
    }
    return userEmail;
}

export async function GET(req: NextRequest) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const search = req.nextUrl.searchParams.toString();
        const suffix = search ? `?${search}` : "";
        const res = await engineFetch(`${ENGINE_URL}/runtime-capabilities${suffix}`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Admin Runtime Capabilities] Engine proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
