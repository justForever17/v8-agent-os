import { NextRequest, NextResponse } from "next/server";

import { generateImageWithDoubao } from "@/lib/volcengine";
import { createEngineChatGatewayStream } from "@/lib/realtime/engine-chat-gateway";
import { buildEngineChatRequestPayload } from "@/lib/realtime/engine-chat-request";
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
        const {
            conversationId,
            currentContent,
            fileUrls,
            provider,
            pythonPayload,
        } = buildEngineChatRequestPayload(payload, userEmail);

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

        const stream = createEngineChatGatewayStream(pythonPayload, userEmail);

        return new NextResponse(stream, {
            headers: {
                "Content-Type": "application/x-ndjson; charset=utf-8",
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                Connection: "keep-alive",
                "x-v8-agent-os-conversation-id": conversationId,
            },
        });
    } catch (error: unknown) {
        console.error("[ClientChatAPI] Fatal Error:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
