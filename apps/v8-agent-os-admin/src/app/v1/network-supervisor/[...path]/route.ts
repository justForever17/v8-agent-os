import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { proxyNetworkPeer } from "@/lib/server/network-peer-proxy";

export const runtime = "nodejs";
type RouteContext = { params: Promise<{ path?: string[] }> };
async function proxy(request: Request, context: RouteContext) {
    const { path = [] } = await context.params;
    return proxyNetworkPeer(request, path, resolveEngineBaseUrl());
}
export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
