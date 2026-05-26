import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    const { id } = await params;
    if (!id) {
        return NextResponse.json({ error: "Message ID is required" }, { status: 400 });
    }

    try {
        const query = req.nextUrl.search || "";
        const res = await fetch(`${ENGINE_URL}/messages/${encodeURIComponent(id)}${query}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Delete Message] Error:", error);
        return NextResponse.json({ error: "Failed to delete message" }, { status: 500 });
    }
}
