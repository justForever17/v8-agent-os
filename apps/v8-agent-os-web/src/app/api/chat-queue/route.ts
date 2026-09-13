import { NextRequest } from "next/server";
import { requireAdminProxyContext, safeAdminProxyFetch } from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";
export const dynamic = "force-dynamic";
export async function GET(req: NextRequest) {
    const context = await requireAdminProxyContext();
    if (context.response) return context.response;
    const query = new URLSearchParams({ session_id: req.nextUrl.searchParams.get("session_id") || "" });
    const cursor = req.nextUrl.searchParams.get("after_ordinal");
    if (cursor !== null) query.set("after_ordinal", cursor);
    const result = await safeAdminProxyFetch(context.context, `/client/chat-queue?${query}`, { cache: "no-store", signal: req.signal }, "/client/chat-queue");
    if (result.errorResponse) return result.errorResponse;
    return relayJsonProxyResponse(result.response, "Queue sync failed");
}
