import { NextRequest, NextResponse } from "next/server";
import { sessionFanoutHub } from "@/lib/realtime/session-fanout";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import {
    buildAuthoritativeSnapshotFingerprint,
    coerceAuthoritativeSessionSnapshot,
    normalizeSessionRuntimeEvent,
    shouldForwardRuntimeEventToRealtimeSurface,
    shouldAuthoritativelyRefreshOnRuntimeEvent,
} from "@v8/session-realtime";
import {
    normalizeRuntimeEventForRealtimeSurface,
    normalizeSnapshotForRealtimeSurface,
} from "@/lib/server/session-realtime-resource";

const ENGINE_URL = resolveEngineBaseUrl();

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function getSnapshotSeq(snapshotData: unknown) {
    return coerceAuthoritativeSessionSnapshot(snapshotData)?.latestSeq || 0;
}

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
            let lastSnapshotFingerprint = "";
            const seenEventIds = new Set<string>();
            let idleBackoffMs = 2500;
            let idlePollCount = 0;
            let snapshotTimer: ReturnType<typeof setTimeout> | null = null;
            let snapshotInflight = false;
            let snapshotPending = false;

            const sendSse = (event: unknown, eventName = "message") => {
                if (closed) return;
                controller.enqueue(
                    encoder.encode(`event: ${eventName}\ndata: ${JSON.stringify(event)}\n\n`)
                );
            };

            const clearSnapshotTimer = () => {
                if (!snapshotTimer) {
                    return;
                }
                clearTimeout(snapshotTimer);
                snapshotTimer = null;
            };

            const fetchAndPushSnapshot = async (force = false) => {
                if (closed) {
                    return;
                }
                if (snapshotInflight) {
                    snapshotPending = true;
                    return;
                }

                snapshotInflight = true;
                snapshotPending = false;

                try {
                    const snapshotRes = await fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
                        method: "GET",
                        headers: { "Content-Type": "application/json" },
                        cache: "no-store",
                    });
                    if (!snapshotRes.ok) {
                        throw new Error(`snapshot request failed: ${snapshotRes.status}`);
                    }

                    const snapshotData = normalizeSnapshotForRealtimeSurface(await snapshotRes.json().catch(() => null));
                    if (!snapshotData) {
                        return;
                    }

                    latestSeq = Math.max(latestSeq, getSnapshotSeq(snapshotData));
                    const nextFingerprint = buildAuthoritativeSnapshotFingerprint(
                        coerceAuthoritativeSessionSnapshot(snapshotData),
                    );
                    if (!force && nextFingerprint === lastSnapshotFingerprint) {
                        return;
                    }

                    lastSnapshotFingerprint = nextFingerprint;
                    sendSse(snapshotData, "snapshot");
                } catch (error) {
                    if (force) {
                        sendSse({ error: String(error) }, "error");
                    } else if (!closed) {
                        console.warn("[Admin Realtime SSE] refresh snapshot failed:", error);
                    }
                } finally {
                    snapshotInflight = false;
                    if (snapshotPending && !closed) {
                        snapshotPending = false;
                        snapshotTimer = setTimeout(() => {
                            snapshotTimer = null;
                            void fetchAndPushSnapshot();
                        }, 480);
                    }
                }
            };

            const queueSnapshotPush = (delayMs = 420) => {
                if (closed) {
                    return;
                }
                if (snapshotTimer) {
                    snapshotPending = true;
                    return;
                }
                snapshotTimer = setTimeout(() => {
                    snapshotTimer = null;
                    void fetchAndPushSnapshot();
                }, delayMs);
            };

            const forwardRuntimeEvent = (event: unknown) => {
                if (!event || typeof event !== "object") {
                    return;
                }

                const normalizedEvent = normalizeSessionRuntimeEvent(event);
                if (!normalizedEvent) {
                    return;
                }
                if (!shouldForwardRuntimeEventToRealtimeSurface(normalizedEvent)) {
                    return;
                }
                const eventId = typeof normalizedEvent.event_id === "string" ? normalizedEvent.event_id : null;
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

                const seq = Number(normalizedEvent.seq || 0);
                if (seq > latestSeq) {
                    latestSeq = seq;
                }
                sendSse(normalizeRuntimeEventForRealtimeSurface(normalizedEvent), "runtime");
                if (shouldAuthoritativelyRefreshOnRuntimeEvent(normalizedEvent)) {
                    queueSnapshotPush();
                }
            };

            const cleanup = sessionFanoutHub.subscribe(id, forwardRuntimeEvent);

            const heartbeat = setInterval(() => {
                sendSse({ ok: true, ts: Date.now() }, "heartbeat");
            }, 15000);

            void fetchAndPushSnapshot(true);

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
                                idleBackoffMs = 2200;
                            } else {
                                idlePollCount += 1;
                                idleBackoffMs = Math.min(2500 + idlePollCount * 1800, 15000);
                            }
                            for (const runtimeEvent of events) {
                                forwardRuntimeEvent(runtimeEvent);
                            }
                        } else {
                            idlePollCount += 1;
                            idleBackoffMs = Math.min(4000 + idlePollCount * 2000, 18000);
                        }
                    } catch (error) {
                        if (!closed) {
                            console.warn("[Admin Realtime SSE] polling runtime events failed:", error);
                        }
                        idlePollCount += 1;
                        idleBackoffMs = Math.min(4000 + idlePollCount * 2000, 18000);
                    }

                    await sleep(idleBackoffMs);
                }
            })();

            req.signal.addEventListener("abort", () => {
                if (closed) return;
                closed = true;
                clearSnapshotTimer();
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
