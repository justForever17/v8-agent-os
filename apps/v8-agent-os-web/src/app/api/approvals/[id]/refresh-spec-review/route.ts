import { NextRequest } from "next/server";
import { requireAdminProxyContext, safeAdminProxyFetch } from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const context = await requireAdminProxyContext();
    if (context.response) return context.response;
    const { id } = await params;
    const result = await safeAdminProxyFetch(context.context, `/approvals/${encodeURIComponent(id)}/refresh-spec-review`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: await req.text(), signal: req.signal,
    });
    if (result.errorResponse) return result.errorResponse;
    return relayJsonProxyResponse(result.response, "Failed to refresh Spec review");
}
