import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveMobileAccessUser } from "@/lib/mobile-auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import { findUserByIdentifier, type AdminUserRecord } from "@/lib/users";

export async function resolveClientUser(req: NextRequest): Promise<AdminUserRecord | null> {
    if (req.headers.has("authorization")) return resolveMobileAccessUser(req);
    const serviceIdentifier = await verifyServiceAuth(req);
    if (serviceIdentifier) {
        const serviceUser = await findUserByIdentifier(serviceIdentifier);
        if (serviceUser) {
            return serviceUser;
        }
    }

    const mobileUser = await resolveMobileAccessUser(req);
    if (mobileUser) {
        return mobileUser;
    }

    const session = await auth();
    const identifier = String(session?.user?.email || "").trim();
    if (!identifier) {
        return null;
    }
    return findUserByIdentifier(identifier);
}

export async function resolveClientUserEmail(req: NextRequest) {
    const user = await resolveClientUser(req);
    return user?.sessionIdentifier || user?.email || user?.login || null;
}

export function unauthorizedClientJson() {
    // Only this BFF authentication rejection is safe to retry after refresh:
    // callers return it before forwarding to the action/Engine owner.
    // Upstream response statuses must not acquire this local marker.
    return NextResponse.json(
        { error: "Unauthorized", code: "auth_pre_execution" },
        { status: 401, headers: { "X-V8-Auth-Stage": "pre_execution" } },
    );
}
