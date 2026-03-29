import { NextRequest } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function GET(req: NextRequest) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const search = req.nextUrl.searchParams.toString();
    const suffix = search ? `?${search}` : "";
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/approvals${suffix}`,
        { method: "GET" },
        "/approvals",
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to fetch approvals");
}
