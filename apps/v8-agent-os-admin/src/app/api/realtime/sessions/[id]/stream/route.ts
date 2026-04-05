import { NextRequest, NextResponse } from "next/server";
import { sessionFanoutHub } from "@/lib/realtime/session-fanout";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    const { id } = await params;
    const encoder = new TextEncoder();
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            let closed = false;
            let latestSeq = 0;
            const seenEventIds = new Set<string>();
            let idleBackoffMs = 10000;
            let idlePollCount = 0;

            const sendSse = (event: unknown, eventName = "message") => {
                if (closed) return;
                controller.enqueue(
                    encoder.encode(`event: ${eventName}\ndata: ${JSON.stringify(event)}\n\n`)
                );
            };

            const forwardRuntimeEvent = (event: unknown) => {
                if (!event || typeof event !== "object") {
                    return;
                }

                const eventRecord = event as Record<string, unknown>;
                const eventId = typeof eventRecord.event_id === "string" ? eventRecord.event_id : null;
                if (eventId) {
                    if (seenEventIds.has(eventId)) {
                        return;
                    }
                    seenEventIds.add(eventId);
                    if (seenEventIds.size > 512) {
                        const first = seenEventIds.values().next();
                        if (!first.done) {
                            seenEventIds.delete(first.value);
                        }
                    }
                }

                const seq = Number(eventRecord.seq || 0);
                if (seq > latestSeq) {
                    latestSeq = seq;
                }
                sendSse(event, "runtime");
            };

            const cleanup = sessionFanoutHub.subscribe(id, forwardRuntimeEvent);

            const heartbeat = setInterval(() => {
                sendSse({ ok: true, ts: Date.now() }, "heartbeat");
            }, 15000);

            void (async () => {
                try {
                    const snapshotRes = await fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
                        method: "GET",
                        headers: { "Content-Type": "application/json" },
                        cache: "no-store",
                    });
                    if (snapshotRes.ok) {
                        const snapshotData = await snapshotRes.json().catch(() => null);
                        if (snapshotData) {
                            latestSeq = Number(snapshotData.latestSeq || snapshotData.snapshot?.latest_seq || 0);
                            sendSse(snapshotData, "snapshot");
                        }
                    }
                } catch (error) {
                    sendSse({ error: String(error) }, "error");
                }
            })();

            void (async () => {
                while (!closed) {
                    try {
                        const eventsRes = await fetch(`${ENGINE_URL}/sessions/${id}/runtime-events?after_seq=${latestSeq}`, {
                            method: "GET",
                            headers: { "Content-Type": "application/json" },
                            cache: "no-store",
                        });

                        if (eventsRes.ok) {
                            const eventsData = await eventsRes.json().catch(() => null);
                            const events = Array.isArray(eventsData?.events) ? eventsData.events : [];
                            if (events.length > 0) {
                                idlePollCount = 0;
                                idleBackoffMs = 3000;
                            } else {
                                idlePollCount += 1;
                                idleBackoffMs = Math.min(10000 + idlePollCount * 5000, 45000);
                            }
                            for (const runtimeEvent of events) {
                                forwardRuntimeEvent(runtimeEvent);
                            }
                        } else {
                            idlePollCount += 1;
                            idleBackoffMs = Math.min(15000 + idlePollCount * 5000, 45000);
                        }
                    } catch (error) {
                        if (!closed) {
                            console.warn("[Admin Realtime SSE] polling runtime events failed:", error);
                        }
                        idlePollCount += 1;
                        idleBackoffMs = Math.min(15000 + idlePollCount * 5000, 45000);
                    }

                    await sleep(idleBackoffMs);
                }
            })();

            req.signal.addEventListener("abort", () => {
                if (closed) return;
                closed = true;
                clearInterval(heartbeat);
                cleanup();
                controller.close();
            });
        }
    });

    return new NextResponse(stream, {
        headers: {
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        }
    });
}
