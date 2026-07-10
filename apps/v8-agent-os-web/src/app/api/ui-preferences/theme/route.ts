import { NextRequest } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

async function proxyTheme(req: NextRequest, method: "GET" | "PUT") {
    const contextResult = await requireAdminProxyContext();
    if (contextResult.response) return contextResult.response;

    const result = await safeAdminProxyFetch(
        contextResult.context,
        "/ui-preferences/theme",
        {
            method,
            headers: method === "PUT" ? { "Content-Type": "application/json" } : undefined,
            body: method === "PUT" ? JSON.stringify(await req.json().catch(() => ({}))) : undefined,
        },
        "/ui-preferences/theme",
    );
    if (result.errorResponse) return result.errorResponse;
    return relayJsonProxyResponse(result.response, "Failed to synchronize theme");
}

export async function GET(req: NextRequest) {
    return proxyTheme(req, "GET");
}

export async function PUT(req: NextRequest) {
    return proxyTheme(req, "PUT");
}
