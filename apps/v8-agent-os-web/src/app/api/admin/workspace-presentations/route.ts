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
    if (!await resolveUserEmail(req)) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    try {
        const response = await engineFetch(`${ENGINE_URL}/workspace-presentations`, { cache: "no-store" });
        return NextResponse.json(await response.json().catch(() => ({})), { status: response.status });
    } catch (error) {
        console.error("[WorkspacePresentationsAPI] GET failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function PUT(req: NextRequest) {
    if (!await resolveUserEmail(req)) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    try {
        const body = await req.json().catch(() => ({}));
        const response = await engineFetch(`${ENGINE_URL}/workspace-presentations`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        return NextResponse.json(await response.json().catch(() => ({})), { status: response.status });
    } catch (error) {
        console.error("[WorkspacePresentationsAPI] PUT failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
