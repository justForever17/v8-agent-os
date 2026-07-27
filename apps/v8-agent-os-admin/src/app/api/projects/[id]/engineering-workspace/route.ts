import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";

const ENGINE_URL = resolveEngineBaseUrl();

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }
    return userEmail;
}

async function proxy(req: NextRequest, id: string, suffix = "", method = "GET") {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    try {
        const response = await fetch(
            `${ENGINE_URL}/projects/${encodeURIComponent(id)}/engineering-workspace${suffix}`,
            { method, headers: { "Content-Type": "application/json" }, cache: "no-store" },
        );
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        console.error("[ProjectEngineeringWorkspaceAPI] proxy failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const { id } = await params;
    return proxy(req, id);
}

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const { id } = await params;
    return proxy(req, id, "/parallel-isolation/enable", "POST");
}
