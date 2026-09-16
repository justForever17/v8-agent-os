import { NextRequest, NextResponse } from "next/server";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

import {
    requireClientProxyContext,
    safeClientProxyFetch,
} from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";

async function proxyTheme(req: NextRequest, method: "GET" | "PUT") {
    const contextResult = await requireClientProxyContext();
    if (contextResult.response) return contextResult.response;

    const result = await safeClientProxyFetch(
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
    // Theme is public presentation data for this local UI. Read it before the
    // automatic login finishes; writes still require the browser session.
    const config = await getClientProxyConfig();
    if (!config.internalSecret) return NextResponse.json({ error: "Local Engine is not configured" }, { status: 503 });
    const result = await safeClientProxyFetch({ ...config, userEmail: "" }, "/ui-preferences/theme", { method: "GET", signal: req.signal });
    if (result.errorResponse) return result.errorResponse;
    return relayJsonProxyResponse(result.response, "Failed to synchronize theme");
}

export async function PUT(req: NextRequest) {
    return proxyTheme(req, "PUT");
}
