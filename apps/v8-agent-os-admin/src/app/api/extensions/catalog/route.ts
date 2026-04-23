import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const search = req.nextUrl.searchParams;
        const params = new URLSearchParams();
        const workspacePath = search.get("workspacePath");
        const workspaceId = search.get("workspaceId");
        const projectId = search.get("projectId");
        if (workspacePath) params.set("workspacePath", workspacePath);
        if (workspaceId) params.set("workspaceId", workspaceId);
        if (projectId) params.set("projectId", projectId);
        const target = `/extensions/catalog${params.toString() ? `?${params.toString()}` : ""}`;
        const { response, data } = await proxyEngineJson(target);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Extensions Catalog] Failed to load catalog:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
