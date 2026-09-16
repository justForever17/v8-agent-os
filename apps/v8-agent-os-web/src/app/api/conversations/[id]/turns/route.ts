import { NextRequest } from "next/server";

import { requireClientProxyContext, safeClientProxyFetch } from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function GET(
    req: NextRequest,
    context: { params: Promise<{ id: string }> },
) {
    const authResult = await requireClientProxyContext();
    if (authResult.response) {
        return authResult.response;
    }

    const { id } = await context.params;
    const result = await safeClientProxyFetch(
        authResult.context,
        `/client/conversations/${encodeURIComponent(id)}/turns${req.nextUrl.search}`,
        { method: "GET", cache: "no-store" },
        "conversation turns",
    );
    if (result.errorResponse) {
        return result.errorResponse;
    }
    return relayJsonProxyResponse(result.response, "Conversation turns request failed");
}
