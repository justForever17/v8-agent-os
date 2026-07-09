import { NextRequest } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id } = await params;
    const search = req.nextUrl.searchParams.toString();
    const suffix = search ? `?${search}` : "";
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/specs/${encodeURIComponent(id)}/analysis${suffix}`,
        { method: "GET" },
        `/specs/${id}/analysis`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to analyze spec");
}
