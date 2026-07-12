import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveMobileAccessUser } from "@/lib/mobile-auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import { findUserByIdentifier, type AdminUserRecord } from "@/lib/users";

export async function resolveClientUser(req: NextRequest): Promise<AdminUserRecord | null> {
    const serviceIdentifier = await verifyServiceAuth(req);
    if (serviceIdentifier) {
        const serviceUser = findUserByIdentifier(serviceIdentifier);
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
    return user?.email || user?.login || null;
}

export function unauthorizedClientJson() {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
}
