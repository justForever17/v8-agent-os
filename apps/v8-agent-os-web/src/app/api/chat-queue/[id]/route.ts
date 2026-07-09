import { NextRequest } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }>;
};

async function relay(req: NextRequest, context: RouteContext, method: "PATCH" | "DELETE") {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id } = await context.params;
    const body = method === "PATCH" ? await req.text() : undefined;
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/client/chat-queue/${encodeURIComponent(id)}`,
        {
            method,
            headers: method === "PATCH" ? { "Content-Type": "application/json" } : undefined,
            body,
        },
        `/client/chat-queue/${id}`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to update queued message");
}

export async function PATCH(req: NextRequest, context: RouteContext) {
    return relay(req, context, "PATCH");
}

export async function DELETE(req: NextRequest, context: RouteContext) {
    return relay(req, context, "DELETE");
}
