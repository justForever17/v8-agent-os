import { NextRequest } from "next/server";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string; stage: string }> },
) {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id, stage } = await params;
    const body = await req.text();
    const result = await safeClientProxyFetch(
        contextResult.context,
        `/specs/${encodeURIComponent(id)}/stages/${encodeURIComponent(stage)}/edit`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        },
        `/specs/${id}/stages/${stage}/edit`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to edit spec stage");
}
