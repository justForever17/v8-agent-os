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

export async function POST(_req: NextRequest, context: RouteContext) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id } = await context.params;
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/client/chat-queue/${encodeURIComponent(id)}/promote`,
        { method: "POST" },
        `/client/chat-queue/${id}/promote`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to promote queued message");
}
