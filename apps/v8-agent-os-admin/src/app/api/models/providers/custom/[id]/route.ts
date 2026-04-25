import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

export async function DELETE(_req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { id } = await context.params;
    const response = await fetch(`${resolveEngineBaseUrl()}/models/providers/custom/${encodeURIComponent(id)}`, {
        method: "DELETE",
    });
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
}
