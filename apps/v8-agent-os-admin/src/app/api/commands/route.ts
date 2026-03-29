import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const response = await fetch(`${resolveEngineBaseUrl()}/commands`, {
            headers: {
                "x-v8-agent-os-secret": resolveInternalSecret(),
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        });

        if (!response.ok) {
            const detail = await response.text().catch(() => "");
            return NextResponse.json(
                { error: detail || "Failed to fetch commands" },
                { status: response.status },
            );
        }

        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to fetch commands" },
            { status: 500 },
        );
    }
}
