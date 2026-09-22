import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

export async function DELETE(_req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session?.user?.email || session.user.adminAuthenticated !== true || session.user.role !== "ADMIN") {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { id } = await context.params;
    const response = await engineFetch(`${resolveEngineBaseUrl()}/models/providers/custom/${encodeURIComponent(id)}`, {
        method: "DELETE",
    });
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
}
