import { NextRequest, NextResponse } from "next/server";
import { requireClientProxyContext, safeClientProxyFetch } from "./client-proxy";
export async function proxyConversationMutation(req: NextRequest, id: string, messageId?: string) {
    const auth = await requireClientProxyContext();
    if (auth.response) return auth.response;
    const body = await req.json().catch(() => null);
    if (!body) return NextResponse.json({ error: "invalid_request" }, { status: 400 });
    const suffix = messageId ? `messages/${encodeURIComponent(messageId)}` : "branches";
    const result = await safeClientProxyFetch(auth.context, `/conversations/${encodeURIComponent(id)}/${suffix}`, {
        method: messageId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json", "x-v8-agent-os-user-email": auth.context.userEmail },
        body: JSON.stringify(body),
    });
    if (result.errorResponse) return result.errorResponse;
    return NextResponse.json(await result.response.json().catch(() => ({})), { status: result.response.status });
}
