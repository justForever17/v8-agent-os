/** Engine is the only owner of users, device credentials and pairing state. */
import { resolveEngineBaseUrl, resolveInternalSecret } from "@admin/lib/server/runtime-config";

export class EngineIdentityError extends Error {
    constructor(readonly code: string, readonly status: number) { super(code); this.name = "EngineIdentityError"; }
}

export async function fetchEngineIdentity(path: string, init: RequestInit = {}) {
    const secret = resolveInternalSecret();
    if (!secret) throw new EngineIdentityError("engine_identity_unavailable", 503);
    const headers = new Headers(init.headers);
    headers.set("x-v8-agent-os-secret", secret);
    if (init.body && typeof init.body === "string") headers.set("content-type", "application/json");
    return fetch(`${resolveEngineBaseUrl()}/client-identity${path}`, { ...init, headers, cache: "no-store", redirect: "error" })
        .catch(() => { throw new EngineIdentityError("engine_identity_unavailable", 503); });
}

export async function engineIdentity<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetchEngineIdentity(path, init);
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new EngineIdentityError(String(value.error || value.detail || "engine_identity_unavailable"), response.status);
    return value as T;
}

export async function fetchEngineClientIdentity(path: string, init: RequestInit = {}) {
    const base = resolveEngineBaseUrl().replace(/\/v1\/?$/, "");
    return fetch(`${base}/api/client${path}`, { ...init, cache: "no-store", redirect: "error" })
        .catch(() => { throw new EngineIdentityError("engine_identity_unavailable", 503); });
}

export async function engineClientIdentity<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetchEngineClientIdentity(path, init);
    const value = await response.json().catch(() => ({}));
    if (!response.ok) throw new EngineIdentityError(String(value.error || value.detail || "engine_identity_unavailable"), response.status);
    return value as T;
}
