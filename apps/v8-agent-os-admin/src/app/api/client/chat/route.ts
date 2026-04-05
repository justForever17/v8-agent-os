import crypto from "crypto";

import { NextRequest, NextResponse } from "next/server";

import { generateImageWithDoubao } from "@/lib/volcengine";
import { createEngineChatGatewayStream } from "@/lib/realtime/engine-chat-gateway";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const payload = await req.json();
        const { messages, data, agentId, tool_outputs } = payload;
        const projectId = payload.project_id ?? payload.projectId ?? data?.projectId;
        const workspaceId = payload.workspace_id ?? payload.workspaceId ?? data?.workspaceId;
        const workspacePath = payload.workspace_path ?? payload.workspacePath ?? data?.workspacePath;
        const scopeHint = payload.scope_hint ?? payload.scopeHint ?? data?.scopeHint;
        const scopeMode = payload.scope_mode ?? payload.scopeMode ?? data?.scopeMode ?? "mixed";
        const conversationId = payload.session_id || payload.conversationId || data?.conversationId || crypto.randomUUID();
        const currentContent = tool_outputs?.[0]?.output || messages?.[messages.length - 1]?.content || "";
        const fileUrls = data?.fileUrls || [];
        const provider = data?.provider;
        const modelName = data?.model;

        if (provider === "volcengine" && currentContent && fileUrls.length > 0) {
            const response = await generateImageWithDoubao(currentContent, fileUrls);
            const aiContent = response.choices?.[0]?.message?.content || "Failed";
            return new NextResponse(
                new ReadableStream({
                    start(controller) {
                        controller.enqueue(new TextEncoder().encode(`${JSON.stringify({ type: "text_chunk", content: aiContent })}\n`));
                        controller.close();
                    },
                }),
                {
                    headers: {
                        "Content-Type": "application/x-ndjson; charset=utf-8",
                        "x-volcengine-image": "true",
                        "x-v8-agent-os-conversation-id": conversationId,
                    },
                },
            );
        }

        const pythonPayload = {
            session_id: conversationId,
            conversationId,
            user_id: userEmail,
            stream: true,
            title: currentContent.substring(0, 30) || "New Chat",
            tool_outputs,
            project_id: projectId,
            workspace_id: workspaceId,
            workspace_path: workspacePath,
            scope_hint: scopeHint,
            scope_mode: scopeMode,
            config: {
                provider,
                model_name: modelName,
                agent_id: agentId,
            },
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            messages: messages.map((message: any) => ({
                role: message.role,
                content: message.content || "",
                tool_call_id: message.tool_call_id,
                name: message.name,
            })),
        };

        const stream = createEngineChatGatewayStream(pythonPayload, userEmail);

        return new NextResponse(stream, {
            headers: {
                "Content-Type": "application/x-ndjson; charset=utf-8",
                "Cache-Control": "no-cache, no-transform",
                Connection: "keep-alive",
                "x-v8-agent-os-conversation-id": conversationId,
            },
        });
    } catch (error: unknown) {
        console.error("[ClientChatAPI] Fatal Error:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
