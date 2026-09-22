import { NextRequest, NextResponse } from "next/server";

import { auth } from "@admin/lib/auth";
import { verifyServiceAuth } from "@admin/lib/service-auth";

export async function resolveAuthorizedUserEmail(req: NextRequest) {
    const serviceUser = await verifyServiceAuth(req);
    if (serviceUser) {
        return serviceUser;
    }
    const session = await auth();
    return session?.user?.adminAuthenticated && session.user.role === "ADMIN"
        ? session.user.email || null
        : null;
}

export function unauthorizedJson() {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
}
