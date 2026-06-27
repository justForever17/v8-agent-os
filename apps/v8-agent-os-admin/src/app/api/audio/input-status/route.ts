import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const response = await fetch(`${resolveEngineBaseUrl()}/audio/input-status`, {
            method: "GET",
            headers: {
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Audio input status proxy failed" },
            { status: 500 },
        );
    }
}
