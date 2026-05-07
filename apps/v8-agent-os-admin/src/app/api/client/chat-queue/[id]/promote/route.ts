import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine } from "@/lib/server/client-proxy";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }>;
};

export async function POST(req: NextRequest, context: RouteContext) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }
    const { id } = await context.params;
    const response = await fetchClientEngine(req, `/chat/queued-messages/${encodeURIComponent(id)}/promote`, {
        method: "POST",
    });
    const json = await response.json().catch(() => ({}));
    return NextResponse.json(json, { status: response.status });
}
