import { NextRequest } from "next/server";
import { requireClientProxyContext, safeClientProxyFetch } from "@/lib/server/proxy/client-proxy";
import { relayJsonProxyResponse } from "@/lib/server/proxy/proxy-response";
export const dynamic = "force-dynamic";
async function relay(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const auth = await requireClientProxyContext();
    if (auth.response) return auth.response;
    const { segments = [] } = await context.params;
    const target = `/client/bg_processes/${segments.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
    const result = await safeClientProxyFetch(auth.context, target, { method: req.method, headers: { "Content-Type": "application/json" }, ...(req.method === "POST" ? { body: await req.text() } : {}), signal: req.signal, cache: "no-store" }, "/client/bg_processes");
    if (result.errorResponse) return result.errorResponse;
    return relayJsonProxyResponse(result.response, "Terminal request failed");
}
export const GET = relay;
export const POST = relay;
