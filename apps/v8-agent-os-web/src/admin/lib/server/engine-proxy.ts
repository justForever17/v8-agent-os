import { NextRequest, NextResponse } from "next/server";

import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@admin/lib/server/runtime-config";
import { verifyServiceAuth } from "@admin/lib/service-auth";
import { findUserById, getSessionIdentifier } from "@admin/lib/users";

export async function resolveAdminIdentity(req?: NextRequest) {
    let userEmail: string | null | undefined = null;
    if (req) {
        userEmail = await verifyServiceAuth(req);
    }
    if (!userEmail) {
        const session = await auth();
        const owner = session?.user?.adminAuthenticated
            && session.user.role === "ADMIN"
            && session.user.id
            ? await findUserById(session.user.id)
            : null;
        userEmail = owner?.role === "ADMIN" ? getSessionIdentifier(owner) : null;
    }
    return userEmail;
}

export async function requireAdminIdentity(req?: NextRequest) {
    const userEmail = await resolveAdminIdentity(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    return null;
}

export async function proxyEngineJson(
    path: string,
    init?: RequestInit,
) {
    const engineBaseUrl = resolveEngineBaseUrl();
    const normalizedPath = engineBaseUrl.endsWith("/v1") && path.startsWith("/v1/")
        ? path.slice(3)
        : path;
    const secret = resolveInternalSecret();
    const headers = new Headers(init?.headers);
    if (secret) headers.set("x-v8-agent-os-secret", secret);
    const response = await fetch(`${engineBaseUrl}${normalizedPath}`, {
        cache: "no-store",
        ...init,
        headers,
    });
    const data = await response.json().catch(() => ({}));
    return { response, data };
}

export async function proxyEngineResponse(path: string, init?: RequestInit) {
    const engineBaseUrl = resolveEngineBaseUrl();
    const normalizedPath = engineBaseUrl.endsWith("/v1") && path.startsWith("/v1/")
        ? path.slice(3)
        : path;
    const secret = resolveInternalSecret();
    const headers = new Headers(init?.headers);
    if (secret) headers.set("x-v8-agent-os-secret", secret);
    return fetch(`${engineBaseUrl}${normalizedPath}`, {
        cache: "no-store",
        ...init,
        headers,
    });
}
