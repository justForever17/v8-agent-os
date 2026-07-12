import { NextRequest } from "next/server";

import { requireAdminProxyContext, safeAdminProxyFetch } from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function GET(
    _req: NextRequest,
    context: { params: Promise<{ id: string }> },
) {
    const authResult = await requireAdminProxyContext();
    if (authResult.response) {
        return authResult.response;
    }

    const { id } = await context.params;
    const result = await safeAdminProxyFetch(
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
