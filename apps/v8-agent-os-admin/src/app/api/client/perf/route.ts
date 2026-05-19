import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { getAdminApiPerfSnapshot } from "@/lib/server/client-perf-metrics";

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }
    return NextResponse.json(getAdminApiPerfSnapshot(), {
        headers: {
            "Cache-Control": "no-store",
        },
    });
}
