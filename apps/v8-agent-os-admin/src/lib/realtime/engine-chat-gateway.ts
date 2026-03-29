import crypto from "crypto";
import { sessionFanoutHub } from "@/lib/realtime/session-fanout";
import { resolveEngineWsBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

function resolveEngineWsUrl() {
    return `${resolveEngineWsBaseUrl().replace(/\/$/, "")}/chat/ws`;
}

function buildWsTicket(userEmail: string) {
    const secret = resolveInternalSecret();
    if (!secret) return null;

    const exp = Math.floor(Date.now() / 1000) + 120;
    const payload = JSON.stringify({
        sub: userEmail,
        aud: "chat_ws",
        exp,
    });
    const payloadB64 = Buffer.from(payload, "utf-8").toString("base64url");
    const signature = crypto.createHmac("sha256", secret).update(payloadB64).digest("base64url");
    return `${payloadB64}.${signature}`;
}

function inferSessionId(raw: unknown, fallbackSessionId?: string) {
    if (!raw || typeof raw !== "object") return fallbackSessionId || null;
    const record = raw as Record<string, unknown>;
    return (
        (typeof record.session_id === "string" ? record.session_id : null) ||
        (typeof record.conversation_id === "string" ? record.conversation_id : null) ||
        (typeof record.sessionId === "string" ? record.sessionId : null) ||
        (typeof record.conversationId === "string" ? record.conversationId : null) ||
        fallbackSessionId ||
        null
    );
}

export function createEngineChatGatewayStream(requestPayload: unknown, userEmail: string) {
    const encoder = new TextEncoder();
    const initialSessionId =
        requestPayload && typeof requestPayload === "object" && typeof (requestPayload as Record<string, unknown>).session_id === "string"
            ? ((requestPayload as Record<string, unknown>).session_id as string)
            : undefined;
    let activeSocket: WebSocket | null = null;

    return new ReadableStream<Uint8Array>({
        async start(controller) {
            let streamClosed = false;

            const closeStream = () => {
                if (streamClosed) return;
                streamClosed = true;
                controller.close();
            };

            const emit = (raw: unknown) => {
                if (streamClosed) return;
                controller.enqueue(encoder.encode(`${JSON.stringify(raw)}\n`));
                const sessionId = inferSessionId(raw, initialSessionId);
                if (sessionId) {
                    sessionFanoutHub.publish(sessionId, raw);
                }
            };

            try {
                const url = new URL(resolveEngineWsUrl());
                const ticket = buildWsTicket(userEmail);
                if (ticket) {
                    url.searchParams.set("ticket", ticket);
                }

                activeSocket = new WebSocket(url);

                activeSocket.onopen = () => {
                    if (initialSessionId) {
                        activeSocket?.send(JSON.stringify({
                            kind: "command",
                            topic: "session.subscribe",
                            session_id: initialSessionId,
                            payload: {
                                include_snapshot: false,
                            },
                        }));
                    }

                    activeSocket?.send(JSON.stringify({
                        kind: "command",
                        topic: "chat.start",
                        request: requestPayload,
                    }));
                };

                activeSocket.onmessage = (messageEvent) => {
                    try {
                        const raw = JSON.parse(String(messageEvent.data));
                        emit(raw);

                        const payload = raw?.payload;
                        const eventType =
                            (payload && typeof payload === "object" && typeof payload.type === "string" ? payload.type : null) ||
                            (typeof raw?.type === "string" ? raw.type : null);

                        if (eventType === "done" || eventType === "error") {
                            activeSocket?.close(1000, "completed");
                        }
                    } catch (error) {
                        emit({ type: "error", error: `Admin gateway parse error: ${String(error)}` });
                        closeStream();
                    }
                };

                activeSocket.onerror = () => {
                    emit({ type: "error", error: "Admin realtime gateway websocket error" });
                    closeStream();
                };

                activeSocket.onclose = () => {
                    closeStream();
                };
            } catch (error) {
                emit({ type: "error", error: `Admin realtime gateway failed: ${String(error)}` });
                closeStream();
            }
        },
        cancel() {
            try {
                activeSocket?.close(4000, "client_abort");
            } catch {
                // no-op
            }
        }
    });
}
