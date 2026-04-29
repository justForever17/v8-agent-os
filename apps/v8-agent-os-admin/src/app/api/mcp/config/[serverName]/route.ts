import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

type RouteContext = {
    params: Promise<{ serverName: string }>;
};

export async function DELETE(_req: Request, context: RouteContext) {
    const session = await auth();
    if (!session) return new NextResponse("Unauthorized", { status: 401 });

    try {
        const { serverName } = await context.params;
        const res = await fetch(`${ENGINE_URL}/mcp/config/${encodeURIComponent(serverName)}`, {
            method: "DELETE",
        });
        const data = await res.json();
        return NextResponse.json(data, { status: res.status });
    } catch {
        return new NextResponse("Internal Error", { status: 500 });
    }
}
