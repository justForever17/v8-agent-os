import { NextRequest } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string; stage: string }> },
) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id, stage } = await params;
    const body = await req.text();
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/specs/${encodeURIComponent(id)}/stages/${encodeURIComponent(stage)}/revise`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        },
        `/specs/${id}/stages/${stage}/revise`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to request spec revision");
}
