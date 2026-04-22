import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const search = req.nextUrl.searchParams;
    const query = search.get("query");
    const refresh = search.get("refresh");
    const workspacePath = search.get("workspacePath");
    const workspaceId = search.get("workspaceId");
    const projectId = search.get("projectId");
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    if (refresh) params.set("refresh", refresh);
    if (workspacePath) params.set("workspacePath", workspacePath);
    if (workspaceId) params.set("workspaceId", workspaceId);
    if (projectId) params.set("projectId", projectId);
    const suffix = params.toString() ? `/extensions/preview?${params.toString()}` : "/extensions/preview";

    try {
        const { response, data } = await proxyEngineJson(suffix);
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to proxy extensions preview:", error);
        return NextResponse.json({ error: "Failed to load extensions preview" }, { status: 500 });
    }
}
