import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine } from "@/lib/server/client-proxy";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }>;
};

async function relay(req: NextRequest, context: RouteContext, method: "PATCH" | "DELETE") {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }
    const { id } = await context.params;
    const body = method === "PATCH" ? await req.text() : undefined;
    const response = await fetchClientEngine(req, `/chat/queued-messages/${encodeURIComponent(id)}`, {
        method,
        headers: method === "PATCH" ? { "Content-Type": "application/json" } : undefined,
        body,
    });
    const json = await response.json().catch(() => ({}));
    return NextResponse.json(json, { status: response.status });
}

export async function PATCH(req: NextRequest, context: RouteContext) {
    return relay(req, context, "PATCH");
}

export async function DELETE(req: NextRequest, context: RouteContext) {
    return relay(req, context, "DELETE");
}
