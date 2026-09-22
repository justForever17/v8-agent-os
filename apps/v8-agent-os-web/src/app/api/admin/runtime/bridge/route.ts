import { NextRequest, NextResponse } from "next/server";

import { resolveAuthorizedUserEmail, unauthorizedJson } from "@admin/lib/server/request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    return NextResponse.json(
        {
            bridgeMode: "admin_only",
            workspaceAssetBaseUrl: "/api/admin/workspace/files",
        },
        {
            headers: {
                "Cache-Control": "no-store",
            },
        },
    );
}
