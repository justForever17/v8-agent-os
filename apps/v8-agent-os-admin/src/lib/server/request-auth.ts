import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { verifyServiceAuth } from "@/lib/service-auth";

export async function resolveAuthorizedUserEmail(req: NextRequest) {
    const serviceUser = await verifyServiceAuth(req);
    if (serviceUser) {
        return serviceUser;
    }
    const session = await auth();
    return session?.user?.email || null;
}

export function unauthorizedJson() {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
}
