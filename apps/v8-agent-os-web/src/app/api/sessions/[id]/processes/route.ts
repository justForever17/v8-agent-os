import { NextRequest } from "next/server";

import { requireClientProxyContext, safeClientProxyFetch } from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function GET(
    _req: NextRequest,
    context: { params: Promise<{ id: string }> },
) {
    const authResult = await requireClientProxyContext();
    if (authResult.response) {
        return authResult.response;
    }

    const { id } = await context.params;
    const result = await safeClientProxyFetch(
        authResult.context,
        `/client/sessions/${encodeURIComponent(id)}/processes`,
        { method: "GET", cache: "no-store" },
        "session processes",
    );
    if (result.errorResponse) {
        return result.errorResponse;
    }
    return relayJsonProxyResponse(result.response, "Session process request failed");
}
