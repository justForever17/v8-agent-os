import { NextRequest, NextResponse } from "next/server";

import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    return NextResponse.json(
        {
            bridgeMode: "admin_only",
            workspaceAssetBaseUrl: "/api/workspace/files",
        },
        {
            headers: {
                "Cache-Control": "no-store",
            },
        },
    );
}
