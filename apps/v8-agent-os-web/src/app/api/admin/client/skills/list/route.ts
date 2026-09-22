import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@admin/lib/server/client-request-auth";
import { resolveEngineOrigin } from "@admin/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const upstream = new URL(`${ENGINE_ORIGIN}/v1/skills/list`);
        for (const key of ["sessionId", "workspacePath", "workspaceId", "projectId"]) {
            const value = req.nextUrl.searchParams.get(key);
            if (value) {
                upstream.searchParams.set(key, value);
            }
        }
        const response = await engineFetch(upstream.toString());
        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("[Client Skills] Error fetching skills list:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
