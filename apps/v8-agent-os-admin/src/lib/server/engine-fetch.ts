import { resolveEngineOrigin, resolveInternalSecret } from "@/lib/server/runtime-config";

/**
 * Server-side fetch boundary for Engine calls. Only the configured canonical
 * Engine origin receives the internal secret; external provider/media URLs are
 * forwarded without V8 credentials. The caller's streaming/body/abort options
 * remain untouched.
 */
export async function engineFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const canonical = new URL(resolveEngineOrigin());
    const target = typeof input === "string"
        ? new URL(input, canonical)
        : input instanceof URL
            ? input
            : new URL(input.url);
    const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
    if (target.origin === canonical.origin) {
        const secret = resolveInternalSecret();
        if (!secret) throw new Error("Engine internal secret is unavailable");
        headers.set("x-v8-agent-os-secret", secret);
    } else {
        // A copied Request may carry V8's service headers. Never forward them
        // to a provider/media origin; its own Authorization remains untouched.
        for (const name of ["x-v8-agent-os-secret", "x-v8-agent-os-user-email", "x-v8-admin-role"]) {
            headers.delete(name);
        }
    }
    return fetch(input, {
        ...init,
        headers,
        redirect: target.origin === canonical.origin ? "error" : (init?.redirect || "error"),
    });
}
