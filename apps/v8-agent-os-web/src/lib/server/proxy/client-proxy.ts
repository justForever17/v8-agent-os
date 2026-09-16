import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";
import { jsonProxyError } from "./proxy-response";

export type ClientProxyContext = {
    engineBaseUrl: string;
    clientApiBaseUrl: string;
    internalSecret: string;
    userEmail: string;
    userRole?: string | null;
};

type ClientProxyContextResult =
    | { context: ClientProxyContext; response?: never }
    | { context?: never; response: NextResponse };

export async function requireClientProxyContext(): Promise<ClientProxyContextResult> {
    const session = await auth();
    const userEmail = session?.user?.email;

    if (!userEmail) {
        return { response: jsonProxyError("Unauthorized", 401) };
    }

    const { engineBaseUrl, clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) {
        return { response: jsonProxyError("Configuration Error", 500) };
    }

    return {
        context: {
            engineBaseUrl,
            clientApiBaseUrl,
            internalSecret,
            userEmail,
            userRole: session.user.role,
        },
    };
}

export function buildClientProxyUrl(context: ClientProxyContext, path: string) {
    const normalized = path.startsWith("/") ? path : `/${path}`;
    const clientPath = normalized.replace(/^\/client\//, "/").replace(/^\/memory\/(artifacts|sources)(?=\/|\?|$)/, "/$1");
    let pathname = clientPath.split(/[?#]/, 1)[0];
    for (let count = 0; count < 4; count++) {
        const decoded = decodeURIComponent(pathname);
        if (decoded === pathname) break;
        pathname = decoded;
    }
    if (pathname.includes("\\") || pathname.startsWith("//") || pathname.split("/").some(segment => segment === "." || segment === "..")) throw new Error("Invalid client route");
    return `${context.clientApiBaseUrl}${clientPath}`;
}

export function buildClientProxyHeaders(context: ClientProxyContext, headers?: HeadersInit) {
    const merged = new Headers(headers);
    merged.set("x-v8-agent-os-secret", context.internalSecret);
    return merged;
}

type SafeClientProxyFetchResult =
    | { response: Response; errorResponse?: never }
    | { response?: never; errorResponse: NextResponse };

export async function safeClientProxyFetch(
    context: ClientProxyContext,
    path: string,
    init: RequestInit = {},
    label = path,
): Promise<SafeClientProxyFetchResult> {
    try {
        const response = await fetch(buildClientProxyUrl(context, path), {
            ...init,
            headers: buildClientProxyHeaders(context, init.headers),
            cache: init.cache ?? "no-store",
            redirect: "error",
        });
        return { response };
    } catch (error) {
        console.error(`[Web Engine Client] ${label} failed:`, error instanceof Error ? error.name : "request_failed");
        return { errorResponse: jsonProxyError("Backend Service Unavailable", 502) };
    }
}
