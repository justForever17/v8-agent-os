import { NextRequest } from "next/server";
import { requireAdminProxyContext, safeAdminProxyFetch } from "@/lib/server/proxy/admin-proxy";
export const dynamic = "force-dynamic";
async function relay(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
    const auth = await requireAdminProxyContext();
    if (auth.response) return auth.response;
    const { path } = await context.params;
    const target = `/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
    const headers: Record<string,string> = {};
    for (const name of ["content-type", "range", "if-none-match", "if-modified-since"]) { const value = req.headers.get(name); if (value) headers[name] = value; }
    const result = await safeAdminProxyFetch(auth.context, target, {
        method: req.method, headers, signal: req.signal, cache: "no-store",
        ...(!["GET","HEAD"].includes(req.method) ? { body: req.body, duplex: "half" } : {}),
    } as RequestInit, "/runtime-admin-proxy");
    if (result.errorResponse) return result.errorResponse;
    const responseHeaders = new Headers();
    for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified", "cache-control"]) { const value = result.response.headers.get(name); if (value) responseHeaders.set(name, value); }
    return new Response(result.response.body, { status: result.response.status, headers: responseHeaders });
}
export const GET = relay;
export const HEAD = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
