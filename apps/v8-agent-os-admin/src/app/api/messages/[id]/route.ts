import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { verifyServiceAuth } from "@/lib/service-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await verifyServiceAuth(req) || (await auth())?.user?.email;
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;
    if (!id) {
        return NextResponse.json({ error: "Message ID is required" }, { status: 400 });
    }

    try {
        const res = await fetch(`${ENGINE_URL}/messages/${id}`, {
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
