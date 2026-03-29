import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";

export async function resolveAdminIdentity(req?: NextRequest) {
    let userEmail: string | null | undefined = null;
    if (req) {
        userEmail = await verifyServiceAuth(req);
    }
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }
    return userEmail;
}

export async function requireAdminIdentity(req?: NextRequest) {
    const userEmail = await resolveAdminIdentity(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    return null;
}

export async function proxyEngineJson(
    path: string,
    init?: RequestInit,
) {
    const response = await fetch(`${resolveEngineBaseUrl()}${path}`, {
        cache: "no-store",
        ...init,
    });
    const data = await response.json().catch(() => ({}));
    return { response, data };
}
