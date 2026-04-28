import { NextRequest, NextResponse } from "next/server";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }
    const { id } = await params;

    try {
        const res = await fetch(`${ENGINE_URL}/safety/allowlist/${encodeURIComponent(id)}/revoke`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Admin Safety Allowlist] Engine proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
