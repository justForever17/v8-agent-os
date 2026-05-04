import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

const ENGINE_TARGET = `${resolveEngineBaseUrl()}/network-supervisor/openai/chat/completions`;
const SSE_HEARTBEAT_MS = 10_000;

function compatStreamHeaders(contentType: string) {
    return {
        "Content-Type": contentType,
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        Connection: "keep-alive",
    };
}

function streamWithHeartbeat(upstream: ReadableStream<Uint8Array> | null) {
    const encoder = new TextEncoder();
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    let heartbeat: ReturnType<typeof setInterval> | null = null;

    return new ReadableStream<Uint8Array>({
        async start(controller) {
            controller.enqueue(encoder.encode(`: v8os-admin-proxy-open ${Date.now()}\n\n`));
            heartbeat = setInterval(() => {
                try {
                    controller.enqueue(encoder.encode(`: v8os-admin-proxy-heartbeat ${Date.now()}\n\n`));
                } catch {
                    // The stream may already be closed by the client.
                }
            }, SSE_HEARTBEAT_MS);
            reader = upstream?.getReader() || null;
            if (!reader) {
                if (heartbeat) {
                    clearInterval(heartbeat);
                    heartbeat = null;
                }
                controller.close();
                return;
            }
            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    if (value) controller.enqueue(value);
                }
                controller.close();
            } catch (error) {
                controller.error(error);
            } finally {
                if (heartbeat) {
                    clearInterval(heartbeat);
                    heartbeat = null;
                }
                reader?.releaseLock();
                reader = null;
            }
        },
        cancel() {
            if (heartbeat) {
                clearInterval(heartbeat);
                heartbeat = null;
            }
            void reader?.cancel().catch(() => undefined);
        },
    });
}

function copyCompatHeaders(req: NextRequest) {
    const headers = new Headers();
    const contentType = req.headers.get("content-type") || "application/json";
    headers.set("Content-Type", contentType);
    const auth = req.headers.get("authorization");
    if (auth) {
        headers.set("Authorization", auth);
    }
    const projectId = req.headers.get("x-v8-project-id");
    const workspaceId = req.headers.get("x-v8-workspace-id");
    const scopeHint = req.headers.get("x-v8-scope-hint");
    if (projectId) headers.set("x-v8-project-id", projectId);
    if (workspaceId) headers.set("x-v8-workspace-id", workspaceId);
    if (scopeHint) headers.set("x-v8-scope-hint", scopeHint);
    const internalSecret = resolveInternalSecret();
    if (internalSecret) {
        headers.set("X-V8-Agent-OS-Secret", internalSecret);
    }
    return headers;
}

export async function POST(req: NextRequest) {
    try {
        const bodyText = await req.text();
        const response = await fetch(ENGINE_TARGET, {
            method: "POST",
            cache: "no-store",
            headers: copyCompatHeaders(req),
            body: bodyText,
        });

        const contentType = response.headers.get("content-type") || "application/json";
        if (contentType.includes("text/event-stream")) {
            return new Response(streamWithHeartbeat(response.body), {
                status: response.status,
                headers: compatStreamHeaders(contentType),
            });
        }

        const payload = await response.text();
        return new NextResponse(payload, {
            status: response.status,
            headers: {
                "Content-Type": contentType,
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        console.error("[Admin Network Supervisor OpenAI Relay] failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
