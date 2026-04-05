import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function GET(req: NextRequest, context: { params: Promise<{ name: string }> }) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const { name } = await context.params;
        const response = await fetch(`${resolveEngineBaseUrl()}/commands/${encodeURIComponent(name)}`, {
            headers: {
                "x-v8-agent-os-secret": resolveInternalSecret(),
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        });

        if (!response.ok) {
            const detail = await response.text().catch(() => "");
            return NextResponse.json(
                { error: detail || "Failed to fetch command preset" },
                { status: response.status },
            );
        }

        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to fetch command preset" },
            { status: 500 },
        );
    }
}
