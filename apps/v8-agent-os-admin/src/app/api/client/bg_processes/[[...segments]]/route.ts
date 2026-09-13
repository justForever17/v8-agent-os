import { NextRequest, NextResponse } from "next/server";
import { resolveClientUser, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";
export const dynamic = "force-dynamic";
async function relay(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const user = await resolveClientUser(req);
    if (!user) return unauthorizedClientJson();
    const { segments = [] } = await context.params;
    if (!segments.length || segments.length > 2 || (segments[1] && !["input", "sensitive-input", "terminate", "resize", "ws-ticket"].includes(segments[1]))) return NextResponse.json({ error: "Unknown terminal operation" }, { status: 400 });
    const response = await fetch(`${resolveEngineBaseUrl()}/bg_processes/${segments.map(encodeURIComponent).join("/")}${req.nextUrl.search}`, {
        method: req.method, headers: { "Content-Type": "application/json", "x-v8-agent-os-secret": resolveInternalSecret(), "x-v8-agent-os-user-email": user.email || user.login, "x-v8-agent-os-user-id": user.id },
        ...(req.method === "POST" ? { body: await req.text() } : {}), cache: "no-store", signal: req.signal,
    });
    return NextResponse.json(await response.json().catch(() => ({})), { status: response.status });
}
export const GET = relay;
export const POST = relay;
