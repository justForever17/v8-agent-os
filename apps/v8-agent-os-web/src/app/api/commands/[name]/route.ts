import { NextRequest } from "next/server";
import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";

export async function GET(_request: NextRequest, context: { params: Promise<{ name: string }> }) {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { name } = await context.params;
    const result = await safeClientProxyFetch(
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
