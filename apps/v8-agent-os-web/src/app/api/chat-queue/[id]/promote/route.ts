import { NextRequest } from "next/server";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }>;
};

export async function POST(_req: NextRequest, context: RouteContext) {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id } = await context.params;
    const result = await safeClientProxyFetch(
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
