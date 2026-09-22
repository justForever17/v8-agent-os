import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { verifyServiceAuth } from "@admin/lib/service-auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.adminAuthenticated && session.user.role === "ADMIN" ? session.user.email : null;
    }
    return userEmail;
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { id } = await params;

    try {
        const res = await engineFetch(`${ENGINE_URL}/sessions/${id}/scope`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[SessionScopeAPI] GET failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function PUT(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { id } = await params;

    try {
        const payload = await req.json();
        const res = await engineFetch(`${ENGINE_URL}/sessions/${id}/scope`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[SessionScopeAPI] PUT failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
