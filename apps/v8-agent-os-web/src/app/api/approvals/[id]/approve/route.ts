import { NextRequest } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) {
        return contextResult.response;
    }

    const { id } = await params;
    const body = await req.json().catch(() => ({}));
    const result = await safeAdminProxyFetch(
        contextResult.context,
        `/approvals/${id}/approve`,
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body ?? {}),
        },
        `/approvals/${id}/approve`,
    );

    if (result.errorResponse) {
        return result.errorResponse;
    }

    return relayJsonProxyResponse(result.response, "Failed to approve request");
}
