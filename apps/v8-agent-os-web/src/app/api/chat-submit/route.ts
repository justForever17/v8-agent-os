import { NextRequest, NextResponse } from "next/server";

import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
    try {
        const contextResult = await requireAdminProxyContext();
        if (contextResult.response) {
            return contextResult.response;
        }

        const body = await req.json();
        const data = body?.data || {};
        const normalizedBody = {
            ...body,
            session_id: body?.session_id || body?.conversationId || data?.conversationId,
            conversationId: body?.conversationId || data?.conversationId || body?.session_id,
            clientMessageId: body?.clientMessageId || data?.clientMessageId,
            project_id: body?.project_id ?? body?.projectId ?? data?.projectId,
            workspace_id: body?.workspace_id ?? body?.workspaceId ?? data?.workspaceId,
            workspace_path: body?.workspace_path ?? body?.workspacePath ?? data?.workspacePath,
            thread_id: body?.thread_id ?? body?.threadId ?? data?.threadId,
            scope_hint: body?.scope_hint ?? body?.scopeHint ?? data?.scopeHint,
            scope_mode: body?.scope_mode ?? body?.scopeMode ?? data?.scopeMode ?? "explicit",
        };

        const result = await safeAdminProxyFetch(
            contextResult.context,
            "/client/chat-submit",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(normalizedBody),
            },
            "/client/chat-submit",
        );

        if (result.errorResponse) {
            return result.errorResponse;
        }

        const response = result.response;
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        console.error("[ChatSubmitProxy] Proxy error:", error);
        return NextResponse.json({ error: "Failed to submit chat run" }, { status: 500 });
    }
}
