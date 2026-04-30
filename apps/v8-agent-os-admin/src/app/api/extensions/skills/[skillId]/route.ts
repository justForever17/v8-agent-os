import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type RouteContext = {
    params: Promise<{ skillId: string }>;
};

export async function DELETE(req: NextRequest, context: RouteContext) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { skillId } = await context.params;
        const search = req.nextUrl.searchParams;
        const params = new URLSearchParams();
        for (const key of ["scope", "workspaceId", "workspacePath", "projectId"]) {
            const value = search.get(key);
            if (value) params.set(key, value);
        }
        const target = `/extensions/skills/${encodeURIComponent(skillId)}${params.toString() ? `?${params.toString()}` : ""}`;
        const { response, data } = await proxyEngineJson(target, { method: "DELETE" });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Extensions Skill Delete] Failed to delete skill:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
