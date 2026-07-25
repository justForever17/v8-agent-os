import { NextRequest, NextResponse } from "next/server";

import type { AdminUserRecord } from "@/lib/users";
import {
    resolveAdminApiBaseUrl,
    resolveClientSurfaceOriginFromRequest,
    resolveEngineBaseUrl,
    resolveInternalSecret,
} from "@/lib/server/runtime-config";
import { resolveClientUser, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { PERSONAL_OWNER_MODE } from "@/lib/users";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";

type ClientContext = {
    user: AdminUserRecord;
    userEmail: string;
};

const ENGINE_NOW_HEADER = "x-v8-engine-now";

function normalizeClientIdentifier(user: AdminUserRecord) {
    return String(user.email || user.login || "").trim();
}

function mergeHeaders(base: HeadersInit | undefined, extra: Record<string, string>) {
    const headers = new Headers(base || {});
    for (const [key, value] of Object.entries(extra)) {
        headers.set(key, value);
    }
    return headers;
}

function buildPassthroughHeaders(response: Response, extra?: HeadersInit) {
    const headers = new Headers(extra || {});
    const engineNow = response.headers.get(ENGINE_NOW_HEADER);
    if (engineNow) {
        headers.set(ENGINE_NOW_HEADER, engineNow);
    }
    return headers;
}

function toClientProxyErrorResponse(error: unknown, fallbackMessage: string) {
    if (error instanceof Error && error.message === "Unauthorized") {
        return unauthorizedClientJson();
    }
    if (error instanceof Error && error.message === "Configuration Error") {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }
    return NextResponse.json(
        { error: error instanceof Error ? error.message : fallbackMessage },
        { status: 502 },
    );
}

export function forbidIfClientAdmin(user?: { role?: string | null }) {
    if (PERSONAL_OWNER_MODE) {
        return false;
    }
    return String(user?.role || "").toUpperCase() === "ADMIN";
}

export async function requireClientContext(req: NextRequest): Promise<ClientContext | NextResponse> {
    const user = await resolveClientUser(req);
    if (!user) {
        return unauthorizedClientJson();
    }
    const userEmail = normalizeClientIdentifier(user);
    if (!userEmail) {
        return unauthorizedClientJson();
    }
    return { user, userEmail };
}

export async function proxyClientAdminJson(
    req: NextRequest,
    targetPath: string,
    init?: RequestInit,
) {
    const startedAt = Date.now();
    try {
        const response = await fetchClientAdmin(req, targetPath, init);
        const payload = await response.json().catch(() => ({}));
        const elapsedMs = Date.now() - startedAt;
        const payloadBytes = jsonSizeBytes(payload);
        recordAdminApiMetric({
            route: `admin:${targetPath}`,
            status: response.status,
            elapsedMs,
            payloadBytes,
            engineElapsedMs: readEngineElapsedMs(payload),
        });
        return NextResponse.json(payload, {
            status: response.status,
            headers: buildPassthroughHeaders(response, {
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(payloadBytes),
            }),
        });
    } catch (error) {
        return toClientProxyErrorResponse(error, "Backend Service Unavailable");
    }
}

export async function proxyClientEngineJson(
    req: NextRequest,
    targetPath: string,
    init?: RequestInit,
) {
    const startedAt = Date.now();
    try {
        const response = await fetchClientEngine(req, targetPath, init);
        const payload = await response.json().catch(() => ({}));
        const elapsedMs = Date.now() - startedAt;
        const payloadBytes = jsonSizeBytes(payload);
        recordAdminApiMetric({
            route: `engine:${targetPath}`,
            status: response.status,
            elapsedMs,
            payloadBytes,
            engineElapsedMs: readEngineElapsedMs(payload),
        });
        return NextResponse.json(payload, {
            status: response.status,
            headers: buildPassthroughHeaders(response, {
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(payloadBytes),
            }),
        });
    } catch (error) {
        return toClientProxyErrorResponse(error, "Engine Service Unavailable");
    }
}

export async function fetchClientAdmin(
    req: NextRequest,
    targetPath: string,
    init?: RequestInit,
) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        throw new Error("Unauthorized");
    }

    const internalSecret = resolveInternalSecret();
    if (!internalSecret) {
        throw new Error("Configuration Error");
    }

    const clientSurfaceOrigin = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: false });

    return fetch(`${resolveAdminApiBaseUrl()}${targetPath}`, {
        cache: "no-store",
        ...init,
        headers: mergeHeaders(init?.headers, {
            "x-v8-agent-os-secret": internalSecret,
            "x-v8-agent-os-user-email": context.userEmail,
            ...(clientSurfaceOrigin ? { "x-v8-client-surface-origin": clientSurfaceOrigin } : {}),
        }),
    });
}

export async function fetchClientEngine(
    req: NextRequest,
    targetPath: string,
    init?: RequestInit,
) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        throw new Error("Unauthorized");
    }

    const clientSurfaceOrigin = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: false });

    return fetch(`${resolveEngineBaseUrl()}${targetPath}`, {
        cache: "no-store",
        ...init,
        headers: mergeHeaders(init?.headers, {
            "x-v8-agent-os-user-email": context.userEmail,
            ...(clientSurfaceOrigin ? { "x-v8-client-surface-origin": clientSurfaceOrigin } : {}),
        }),
    });
}

export async function proxyClientAdminStream(
    req: NextRequest,
    targetPath: string,
    defaultContentType: string,
    init?: RequestInit,
) {
    try {
        const response = await fetchClientAdmin(req, targetPath, {
            ...init,
            signal: req.signal,
        });

        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return NextResponse.json(
                { error: detail || "Realtime stream unavailable" },
                { status: response.status || 502 },
            );
        }

        return new NextResponse(response.body, {
            status: response.status,
            headers: buildPassthroughHeaders(response, {
                "Content-Type": response.headers.get("Content-Type") || defaultContentType,
                "Cache-Control": response.headers.get("Cache-Control") || "no-cache, no-transform",
                "X-Accel-Buffering": response.headers.get("X-Accel-Buffering") || "no",
                "Connection": response.headers.get("Connection") || "keep-alive",
            }),
        });
    } catch (error) {
        return toClientProxyErrorResponse(error, "Backend Service Unavailable");
    }
}

export async function proxyClientEngineStream(
    req: NextRequest,
    targetPath: string,
    defaultContentType: string,
    init?: RequestInit,
) {
    try {
        const response = await fetchClientEngine(req, targetPath, {
            ...init,
            signal: req.signal,
        });

        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return NextResponse.json(
                { error: detail || "Realtime stream unavailable" },
                { status: response.status || 502 },
            );
        }

        return new NextResponse(response.body, {
            status: response.status,
            headers: buildPassthroughHeaders(response, {
                "Content-Type": response.headers.get("Content-Type") || defaultContentType,
                "Cache-Control": response.headers.get("Cache-Control") || "no-cache, no-transform",
                "X-Accel-Buffering": response.headers.get("X-Accel-Buffering") || "no",
                "Connection": response.headers.get("Connection") || "keep-alive",
            }),
        });
    } catch (error) {
        return toClientProxyErrorResponse(error, "Engine Service Unavailable");
    }
}
