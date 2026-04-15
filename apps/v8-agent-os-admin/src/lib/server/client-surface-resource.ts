import crypto from "crypto";
import type { NextRequest } from "next/server";

import {
    resolveAdminApiBaseUrl,
    resolveInternalSecret,
    resolveReachableAdminPublicBaseUrl,
    resolveReachableClientSurfaceOrigin,
} from "@/lib/server/runtime-config";

const DEFAULT_SURFACE_RESOURCE_TTL_SECONDS = 60 * 10;
const INTERNAL_SURFACE_USER_EMAIL = "surface-resource@internal";

function mergeHeaders(base: HeadersInit | undefined, extra: Record<string, string>) {
    const headers = new Headers(base || {});
    for (const [key, value] of Object.entries(extra)) {
        headers.set(key, value);
    }
    return headers;
}

function normalizeClientSurfacePath(path: string) {
    const trimmed = String(path || "").trim();
    if (!trimmed) {
        return "";
    }
    return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

function buildSignature(path: string, exp: string) {
    const secret = resolveInternalSecret();
    if (!secret) {
        return "";
    }
    return crypto.createHmac("sha256", secret).update(`${path}|${exp}`).digest("hex");
}

export function buildSignedClientSurfaceUrl(
    path: string,
    options?: { ttlSeconds?: number; absolute?: boolean; publicBaseUrl?: string },
) {
    const normalizedPath = normalizeClientSurfacePath(path);
    if (!normalizedPath) {
        return "";
    }

    const ttlSeconds = Math.max(30, Number(options?.ttlSeconds || DEFAULT_SURFACE_RESOURCE_TTL_SECONDS) || DEFAULT_SURFACE_RESOURCE_TTL_SECONDS);
    const exp = String(Math.floor(Date.now() / 1000) + ttlSeconds);
    const sig = buildSignature(normalizedPath, exp);
    if (!sig) {
        return "";
    }

    const query = `v8exp=${encodeURIComponent(exp)}&v8sig=${encodeURIComponent(sig)}`;
    const signedPath = normalizedPath.includes("?") ? `${normalizedPath}&${query}` : `${normalizedPath}?${query}`;
    if (options?.absolute === false) {
        return signedPath;
    }
    const publicBase = resolveReachableClientSurfaceOrigin(String(options?.publicBaseUrl || ""))
        || resolveReachableAdminPublicBaseUrl();
    if (!publicBase) {
        return "";
    }
    return `${publicBase}${signedPath}`;
}

export function verifySignedClientSurfaceRequest(req: NextRequest) {
    const path = normalizeClientSurfacePath(req.nextUrl.pathname);
    const exp = String(req.nextUrl.searchParams.get("v8exp") || "").trim();
    const sig = String(req.nextUrl.searchParams.get("v8sig") || "").trim().toLowerCase();
    if (!path || !exp || !sig) {
        return false;
    }

    const expSeconds = Number(exp);
    if (!Number.isFinite(expSeconds) || expSeconds <= Math.floor(Date.now() / 1000)) {
        return false;
    }

    const expected = buildSignature(path, exp).toLowerCase();
    if (!expected) {
        return false;
    }

    try {
        return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(sig));
    } catch {
        return false;
    }
}

export async function fetchSignedClientAdminPath(
    targetPath: string,
    init?: RequestInit,
) {
    const internalSecret = resolveInternalSecret();
    if (!internalSecret) {
        throw new Error("Configuration Error");
    }

    return fetch(`${resolveAdminApiBaseUrl()}${targetPath}`, {
        cache: "no-store",
        ...init,
        headers: mergeHeaders(init?.headers, {
            "x-v8-agent-os-secret": internalSecret,
            "x-v8-agent-os-user-email": INTERNAL_SURFACE_USER_EMAIL,
        }),
    });
}
