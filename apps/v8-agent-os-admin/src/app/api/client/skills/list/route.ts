import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const response = await fetch(`${ENGINE_ORIGIN}/v1/skills/list`);
        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("[Client Skills] Error fetching skills list:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
