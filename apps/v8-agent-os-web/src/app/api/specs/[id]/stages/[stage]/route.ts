import { NextRequest } from "next/server";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string; stage: string }> },
) {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id, stage } = await params;
    const search = req.nextUrl.searchParams.toString();
    const suffix = search ? `?${search}` : "";
    const result = await safeClientProxyFetch(
        contextResult.context,
        `/specs/${encodeURIComponent(id)}/stages/${encodeURIComponent(stage)}${suffix}`,
        { method: "GET" },
        `/specs/${id}/stages/${stage}`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to fetch spec stage");
}
