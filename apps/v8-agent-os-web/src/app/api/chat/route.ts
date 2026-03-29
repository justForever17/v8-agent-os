import { NextRequest, NextResponse } from "next/server";
import {
    requireAdminProxyContext,
    safeAdminProxyFetch,
} from "@/lib/server/proxy/admin-proxy";
import {
    jsonProxyError,
    relayStreamProxyResponse,
} from "@/lib/server/proxy/proxy-response";

export const runtime = "nodejs"; // Use Node.js runtime for stability

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
            project_id: body?.project_id ?? body?.projectId ?? data?.projectId,
            workspace_id: body?.workspace_id ?? body?.workspaceId ?? data?.workspaceId,
            workspace_path: body?.workspace_path ?? body?.workspacePath ?? data?.workspacePath,
            scope_hint: body?.scope_hint ?? body?.scopeHint ?? data?.scopeHint,
            scope_mode: body?.scope_mode ?? body?.scopeMode ?? data?.scopeMode ?? "mixed",
        };

        const result = await safeAdminProxyFetch(
            contextResult.context,
            "/chat",
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(normalizedBody),
            },
            "/chat",
        );

        if (result.errorResponse) {
            return result.errorResponse;
        }

        const response = result.response;

        if (!response.ok) {
            const errorText = await response.text();
            console.error(`[ChatProxy] Backend error: ${response.status} ${errorText}`);
            return NextResponse.json(
                { error: `Backend error: ${response.statusText}` },
                { status: response.status }
            );
        }
        if (!response.body) {
            return jsonProxyError("Failed to connect to backend", 502);
        }

        return relayStreamProxyResponse(response, {
            cacheControl: "no-cache, no-transform",
            defaultContentType: "application/x-ndjson; charset=utf-8",
            preserveHeaders: ["x-v8-agent-os-conversation-id"],
            successStatus: 200,
            applyHeaders: (headers) => {
                headers.set("Connection", "keep-alive");
            },
        });
    } catch (error) {
        console.error("[ChatProxy] Proxy error:", error);
        return NextResponse.json(
            { error: "Failed to connect to backend" },
            { status: 500 }
        );
    }
}
