import { NextRequest, NextResponse } from "next/server";
import { sessionFanoutHub } from "@/lib/realtime/session-fanout";
import {
    resolveClientSurfaceOriginFromRequest,
    resolveEngineBaseUrl,
} from "@/lib/server/runtime-config";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import {
    buildAuthoritativeSnapshotFingerprint,
    coerceAuthoritativeSessionSnapshot,
    normalizeSessionRuntimeEvent,
    shouldForwardRuntimeEventToRealtimeSurface,
    shouldAuthoritativelyRefreshOnRuntimeEvent,
} from "@v8/session-realtime";
import { SessionRuntimeEventContiguousCursor } from "@v8/session-realtime/event-sequence";
import {
    normalizeRuntimeEventForRealtimeSurface,
    normalizeSnapshotForRealtimeSurface,
} from "@/lib/server/session-realtime-resource";
import {
    buildRuntimeEventDeliveryIdentity,
    RuntimeEventGapRecoveryThrottle,
    shouldRequestSnapshotForEmptyEventPage,
    shouldDeliverRuntimeEventObservation,
} from "@/lib/server/runtime-event-delivery";

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
    const publicBaseUrl = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: true });
    const surface = req.nextUrl.searchParams.get("surface");
    const compactPhone = surface === "phone"
        || surface === "desktop"
        || req.nextUrl.searchParams.get("compact") === "1";
    const encoder = new TextEncoder();
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            let closed = false;
            const runtimeCursor = new SessionRuntimeEventContiguousCursor();
            const gapRecovery = new RuntimeEventGapRecoveryThrottle();
            let lastSnapshotFingerprint = "";
            const seenDeliveryIdentities = new Map<string, number>();
            let idleBackoffMs = 350;
            let idlePollCount = 0;
            let lastForwardedAt = 0;
            let snapshotTimer: ReturnType<typeof setTimeout> | null = null;
            let snapshotInflight = false;
            let snapshotPending = false;
            let eventCounter = 0;

            const sendSse = (event: unknown, eventName = "message") => {
                if (closed) return;
                const adminForwardedAt = new Date().toISOString();
                const proxyFlushAt = new Date().toISOString();
                eventCounter += 1;
                const eventId = `${id}:${eventCounter}`;
                const payload = event && typeof event === "object"
                    ? {
                        ...(event as Record<string, unknown>),
                        _diagnostics: {
                            ...asRecord((event as Record<string, unknown>)._diagnostics),
                            adminForwardedAt,
                            proxyFlushAt,
                            proxyEventId: eventId,
                        },
                    }
                    : event;
                lastForwardedAt = Date.now();
                controller.enqueue(
                    encoder.encode(`id: ${eventId}\nevent: ${eventName}\ndata: ${JSON.stringify(payload)}\n\n`)
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
                    const snapshotStartedAt = Date.now();
                    const snapshotRes = await fetch(
                        `${ENGINE_URL}/sessions/${encodeURIComponent(id)}/snapshot${compactPhone ? "?compact=1" : ""}`,
                        {
                            method: "GET",
                            headers: { "Content-Type": "application/json" },
                            cache: "no-store",
                        },
                    );
                    if (!snapshotRes.ok) {
                        throw new Error(`snapshot request failed: ${snapshotRes.status}`);
                    }

                    const snapshotData = normalizeSnapshotForRealtimeSurface(
                        await snapshotRes.json().catch(() => null),
                        { publicBaseUrl, compactPhone },
                    );
                    recordAdminApiMetric({
                        route: "admin.realtime.stream.snapshot",
                        status: snapshotRes.status,
                        elapsedMs: Date.now() - snapshotStartedAt,
                        payloadBytes: jsonSizeBytes(snapshotData),
                        engineElapsedMs: readEngineElapsedMs(snapshotData),
                    });
                    if (!snapshotData) {
                        return;
                    }

                    const snapshotContinuity = runtimeCursor.coverThrough(getSnapshotSeq(snapshotData));
                    if (gapRecovery.shouldRequestSnapshot(snapshotContinuity.gap)) {
                        queueSnapshotPush(480);
                    }
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
                if (snapshotInflight) {
                    snapshotPending = true;
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
                const seq = Number(normalizedEvent.seq || 0);
                const continuity = runtimeCursor.observe(seq);
                const gapSnapshotRequested = gapRecovery.shouldRequestSnapshot(continuity.gap);
                if (gapSnapshotRequested) {
                    queueSnapshotPush(120);
                }
                if (!shouldDeliverRuntimeEventObservation(continuity)) {
                    return;
                }
                if (!shouldForwardRuntimeEventToRealtimeSurface(normalizedEvent)) {
                    return;
                }
                const surfaceEvent = normalizeRuntimeEventForRealtimeSurface(normalizedEvent, { publicBaseUrl });
                const topic = String(surfaceEvent && typeof surfaceEvent === "object" ? (surfaceEvent as Record<string, unknown>).topic || "" : "").trim().toLowerCase();
                const dedupeKey = String(surfaceEvent && typeof surfaceEvent === "object" ? (surfaceEvent as Record<string, unknown>).dedupeKey || "" : "").trim();
                const deliveryIdentity = buildRuntimeEventDeliveryIdentity({
                    eventId: normalizedEvent.event_id,
                    dedupeKey,
                    topic,
                });
                if (deliveryIdentity) {
                    if (seenDeliveryIdentities.has(deliveryIdentity)) {
                        return;
                    }
                    seenDeliveryIdentities.set(deliveryIdentity, seq || Date.now());
                    if (seenDeliveryIdentities.size > 512) {
                        const first = seenDeliveryIdentities.keys().next();
                        if (!first.done) {
                            seenDeliveryIdentities.delete(first.value);
                        }
                    }
                }
                const deliveryEvent = continuity.gap && surfaceEvent && typeof surfaceEvent === "object"
                    ? {
                        ...(surfaceEvent as Record<string, unknown>),
                        _diagnostics: {
                            ...asRecord((surfaceEvent as Record<string, unknown>)._diagnostics),
                            runtimeSequenceGap: continuity.gap,
                            runtimeSequenceGapRecovery: gapSnapshotRequested
                                ? "authoritative_snapshot_requested"
                                : "authoritative_snapshot_throttled",
                        },
                    }
                    : surfaceEvent;
                sendSse(deliveryEvent, "runtime");
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
                        const eventsStartedAt = Date.now();
                        const eventsRes = await fetch(`${ENGINE_URL}/sessions/${encodeURIComponent(id)}/runtime-events?after_seq=${runtimeCursor.contiguousSeq}`, {
                            method: "GET",
                            headers: { "Content-Type": "application/json" },
                            cache: "no-store",
                        });

                        if (eventsRes.ok) {
                            const eventsData = await eventsRes.json().catch(() => null);
                            recordAdminApiMetric({
                                route: "admin.realtime.stream.events",
                                status: eventsRes.status,
                                elapsedMs: Date.now() - eventsStartedAt,
                                payloadBytes: jsonSizeBytes(eventsData),
                                engineElapsedMs: readEngineElapsedMs(eventsData),
                            });
                            const events = Array.isArray(eventsData?.events) ? eventsData.events : [];
                            const latestEventsSeq = Number(eventsData?.latestSeq || 0) || 0;
                            if (events.length > 0) {
                                idlePollCount = 0;
                                idleBackoffMs = 260;
                            } else if (shouldRequestSnapshotForEmptyEventPage(
                                latestEventsSeq,
                                runtimeCursor.contiguousSeq,
                            )) {
                                // The replay page is empty but the durable
                                // high-water mark moved. Ask the snapshot
                                // path to cover the gap before polling again.
                                queueSnapshotPush(120);
                                idlePollCount = 0;
                                idleBackoffMs = 320;
                            } else {
                                const recentlyForwarded = Date.now() - lastForwardedAt < 6000;
                                if (recentlyForwarded) {
                                    idlePollCount = 0;
                                    idleBackoffMs = 320;
                                } else {
                                    idlePollCount += 1;
                                    idleBackoffMs = Math.min(500 + idlePollCount * 450, 2400);
                                }
                            }
                            for (const runtimeEvent of events) {
                                forwardRuntimeEvent(runtimeEvent);
                            }
                        } else {
                            idlePollCount += 1;
                            idleBackoffMs = Math.min(900 + idlePollCount * 800, 4200);
                        }
                    } catch (error) {
                        if (!closed) {
                            console.warn("[Admin Realtime SSE] polling runtime events failed:", error);
                        }
                        idlePollCount += 1;
                        idleBackoffMs = Math.min(1200 + idlePollCount * 1000, 5200);
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
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    });
}
