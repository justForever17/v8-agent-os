import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";

export async function GET() {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const result = await safeClientProxyFetch(
        contextResult.context,
        "/commands",
        { method: "GET" },
        "/commands",
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to fetch commands");
}
