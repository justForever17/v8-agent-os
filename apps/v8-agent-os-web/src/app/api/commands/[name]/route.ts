import { NextRequest } from "next/server";
import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";

export async function GET(_request: NextRequest, context: { params: Promise<{ name: string }> }) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { name } = await context.params;
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/commands/${encodeURIComponent(name)}`,
        { method: "GET" },
        `/commands/${name}`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to fetch command preset");
}
