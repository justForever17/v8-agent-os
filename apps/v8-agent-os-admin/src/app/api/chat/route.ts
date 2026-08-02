import { NextRequest, NextResponse } from "next/server";
import { generateImageWithDoubao } from "@/lib/volcengine";
import crypto from 'crypto';
import { createEngineChatGatewayStream } from "@/lib/realtime/engine-chat-gateway";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const payload = await req.json();
        const { messages, data, agentId, tool_outputs } = payload;
        const projectId = payload.project_id ?? payload.projectId ?? data?.projectId;
        const workspaceId = payload.workspace_id ?? payload.workspaceId ?? data?.workspaceId;
        const workspacePath = payload.workspace_path ?? payload.workspacePath ?? data?.workspacePath;
        const scopeHint = payload.scope_hint ?? payload.scopeHint ?? data?.scopeHint;
        const scopeMode = payload.scope_mode ?? payload.scopeMode ?? data?.scopeMode ?? "explicit";
        
        const conversationId = payload.session_id || payload.conversationId || data?.conversationId || crypto.randomUUID();
        const currentContent = tool_outputs?.[0]?.output || messages?.[messages.length - 1]?.content || "";
        const fileUrls = data?.fileUrls || [];
        
        const provider = data?.provider;
        const modelName = data?.model;
        
        // 1. Volcengine Exception (Keep Native Logic for Images if really needed, but ideally engine handles it)
        // Leaving this here for backward compatibility if the frontend still hardcodes provider = volcengine for images
        if (provider === 'volcengine' && currentContent && fileUrls.length > 0) {
            const response = await generateImageWithDoubao(currentContent, fileUrls);
            const aiContent = response.choices?.[0]?.message?.content || "Failed";
            return new NextResponse(
                new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(JSON.stringify({type: 'text_chunk', content: aiContent}) + "\n")); c.close(); } }),
                { headers: { 'Content-Type': 'application/x-ndjson; charset=utf-8', 'x-volcengine-image': 'true', 'x-v8-agent-os-conversation-id': conversationId! } }
            );
        }

        // 2. Build Python Body Payload
        // We defer provider, model, system prompt resolution entirely to the Python engine where possible.
        // If agent_id is passed, Python engine looks up the agent settings.
        const pythonPayload = {
            session_id: conversationId,
            conversationId,
            user_id: userEmail || "anonymous", // Use authenticated email
            stream: true,
            title: currentContent.substring(0, 30) || "New Chat", // Pass title for session creation
            tool_outputs,
            project_id: projectId,
            workspace_id: workspaceId,
            workspace_path: workspacePath,
            scope_hint: scopeHint,
            scope_mode: scopeMode,
            config: {
                provider,         // Let python fallback to default if None
                model_name: modelName, 
                agent_id: agentId
            },
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            messages: messages.map((m: any) => ({
                id: typeof m.id === "string" ? m.id : (m.messageId || m.message_id),
                role: m.role,
                content: m.content || "",
                tool_call_id: m.tool_call_id,
                name: m.name
            }))
        };

        const stream = createEngineChatGatewayStream(pythonPayload, userEmail || "anonymous");

        return new NextResponse(stream, {
            headers: {
                'Content-Type': 'application/x-ndjson; charset=utf-8',
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
                'x-v8-agent-os-conversation-id': conversationId!
            }
        });

    } catch (e: unknown) {
        console.error('[ChatAPI] Fatal Error:', e);
        return NextResponse.json({ error: String(e) }, { status: 500 });
    }
}

