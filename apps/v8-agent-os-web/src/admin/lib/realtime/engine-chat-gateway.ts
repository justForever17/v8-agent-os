import crypto from "crypto";
import { sessionFanoutHub } from "@admin/lib/realtime/session-fanout";
import { resolveEngineWsBaseUrl, resolveInternalSecret } from "@admin/lib/server/runtime-config";
import { normalizeRuntimeEventForRealtimeSurface } from "@admin/lib/server/session-realtime-resource";
import { buildSessionStreamUiEvent } from "@v8/session-realtime";

const ENGINE_CHAT_CONNECT_TIMEOUT_MS = 10_000;

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

export function createEngineChatGatewayStream(requestPayload: unknown, userEmail: string, signal?: AbortSignal) {
    const encoder = new TextEncoder();
    const initialSessionId = inferSessionId(requestPayload) || undefined;
    let activeSocket: WebSocket | null = null;
    let cancelStream = () => {};

    return new ReadableStream<Uint8Array>({
        start(controller) {
            let closed = false;
            let runId: string | undefined;
            let connectTimer: ReturnType<typeof setTimeout> | undefined;

            const release = () => {
                clearTimeout(connectTimer);
                signal?.removeEventListener("abort", abortStream);
                if (!activeSocket) return;
                activeSocket.onopen = null;
                activeSocket.onmessage = null;
                activeSocket.onerror = null;
                activeSocket.onclose = null;
                try { activeSocket.close(1000, "gateway_detached"); } catch { /* already closed */ }
            };
            const closeStream = () => {
                if (closed) return;
                closed = true;
                release();
                controller.close();
            };
            const abortStream = () => closeStream();
            cancelStream = () => {
                if (closed) return;
                closed = true;
                release();
            };
            const failTransport = (code: string, error: string) => {
                if (closed) return;
                // Transport failure is not an Engine terminal event. Keep it
                // outside runtime normalization/fanout so the run stays intact.
                controller.enqueue(encoder.encode(`${JSON.stringify({
                    type: "transport_error", code, error,
                    sessionId: initialSessionId,
                    ...(runId ? { runId } : {}),
                    unknownOutcome: true, retryable: false, recovery: "resync",
                })}\n`));
                closeStream();
            };

            try {
                if (signal?.aborted) { closeStream(); return; }
                signal?.addEventListener("abort", abortStream, { once: true });
                const url = new URL(resolveEngineWsUrl());
                const ticket = buildWsTicket(userEmail);
                if (ticket) url.searchParams.set("ticket", ticket);
                activeSocket = new WebSocket(url);

                activeSocket.onopen = () => {
                    if (closed) return;
                    clearTimeout(connectTimer);
                    try {
                        if (initialSessionId) {
                            activeSocket?.send(JSON.stringify({
                                kind: "command", topic: "session.subscribe",
                                session_id: initialSessionId, payload: { include_snapshot: false },
                            }));
                        }
                        activeSocket?.send(JSON.stringify({
                            kind: "command", topic: "chat.start", request: requestPayload,
                        }));
                    } catch {
                        failTransport("engine_stream_disconnected", "Engine connection failed while submitting the request; resync this session before continuing.");
                    }
                };
                activeSocket.onmessage = (messageEvent) => {
                    if (closed) return;
                    try {
                        const raw = JSON.parse(String(messageEvent.data));
                        if (raw?.kind === "error" && !raw.payload?.type) {
                            failTransport("engine_request_rejected", "Engine rejected the streaming request; resync this session before continuing.");
                            return;
                        }
                        const event = buildSessionStreamUiEvent(raw, { locale: "zh-CN" });
                        if (!event) return;
                        runId = event.run_id || runId;
                        const payload = normalizeRuntimeEventForRealtimeSurface(event);
                        controller.enqueue(encoder.encode(`${JSON.stringify(payload)}\n`));
                        const sessionId = inferSessionId(payload, initialSessionId);
                        if (sessionId) sessionFanoutHub.publish(sessionId, payload);
                        if (event.type === "done" || event.type === "error") closeStream();
                    } catch {
                        failTransport("engine_stream_invalid", "Engine returned an unreadable stream; resync this session before continuing.");
                    }
                };
                activeSocket.onerror = () => failTransport(
                    "engine_stream_disconnected",
                    "Engine connection was interrupted; the task outcome is unknown. Resync this session before continuing.",
                );
                activeSocket.onclose = () => failTransport(
                    "engine_stream_disconnected",
                    "Engine connection closed before completion; the task outcome is unknown. Resync this session before continuing.",
                );
                connectTimer = setTimeout(() => failTransport(
                    "engine_connect_timeout", "Engine connection timed out; resync this session before continuing.",
                ), ENGINE_CHAT_CONNECT_TIMEOUT_MS);
                // No business idle deadline: Engine has no periodic heartbeat
                // while it is reasoning, executing tools, or awaiting approval.
            } catch {
                failTransport("engine_stream_disconnected", "Engine connection is unavailable; resync this session before continuing.");
            }
        },
        cancel() { cancelStream(); },
    });
}
