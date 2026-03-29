import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";
import { jsonProxyError } from "./proxy-response";

export type AdminProxyContext = {
    adminApiBaseUrl: string;
    internalSecret: string;
    userEmail: string;
    userRole?: string | null;
};

type AdminProxyContextResult =
    | { context: AdminProxyContext; response?: never }
    | { context?: never; response: NextResponse };

export async function requireAdminProxyContext(): Promise<AdminProxyContextResult> {
    const session = await auth();
    const userEmail = session?.user?.email;

    if (!userEmail) {
        return { response: jsonProxyError("Unauthorized", 401) };
    }

    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return { response: jsonProxyError("Configuration Error", 500) };
    }

    return {
        context: {
            adminApiBaseUrl,
            internalSecret,
            userEmail,
            userRole: session.user.role,
        },
    };
}

export function buildAdminProxyUrl(context: AdminProxyContext, path: string) {
    return `${context.adminApiBaseUrl}${path}`;
}

export function buildAdminProxyHeaders(context: AdminProxyContext, headers?: HeadersInit) {
    const merged = new Headers(headers);
    merged.set("x-v8-agent-os-secret", context.internalSecret);
    merged.set("x-v8-agent-os-user-email", context.userEmail);
    return merged;
}

type SafeAdminProxyFetchResult =
    | { response: Response; errorResponse?: never }
    | { response?: never; errorResponse: NextResponse };

export async function safeAdminProxyFetch(
    context: AdminProxyContext,
    path: string,
    init: RequestInit = {},
    label = path,
): Promise<SafeAdminProxyFetchResult> {
    try {
        const response = await fetch(buildAdminProxyUrl(context, path), {
            ...init,
            headers: buildAdminProxyHeaders(context, init.headers),
            cache: init.cache ?? "no-store",
        });
        return { response };
    } catch (error) {
        console.error(`[Web Admin Proxy] ${label} failed:`, error);
        return { errorResponse: jsonProxyError("Backend Service Unavailable", 502) };
    }
}
