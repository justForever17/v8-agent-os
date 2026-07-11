import { requireAdminProxyContext, safeAdminProxyFetch } from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs";

export async function GET() {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) return contextResult.response;
    const result = await safeAdminProxyFetch(
        contextResult.context,
        "/plugins/catalog",
        { method: "GET" },
        "/plugins/catalog",
    );
    if (result.errorResponse) return result.errorResponse;
    return relayJsonProxyResponse(result.response, "Failed to fetch plugin catalog");
}
