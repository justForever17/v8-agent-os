import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { jsonSizeBytes, readEngineElapsedMs, recordAdminApiMetric } from "@/lib/server/client-perf-metrics";
import { resolveClientSurfaceOriginFromRequest, resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { normalizeMessageForRealtimeSurface, normalizeProcessForRealtimeSurface, normalizeSnapshotForRealtimeSurface } from "@/lib/server/session-realtime-resource";
import { applyCanonicalSourceGroup } from "@/lib/server/source-group";

const ENGINE_URL = resolveEngineBaseUrl();
const ENGINE_NOW_HEADER = "x-v8-engine-now";

function asRecord(value: unknown) {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function stripMessagesForProjection(value: Record<string, unknown>) {
    const projection = { ...value };
    delete projection.messages;
    delete projection.timeline;
    const snapshot = asRecord(projection.snapshot);
    if (Object.keys(snapshot).length > 0) {
        const nextSnapshot = { ...snapshot };
        delete nextSnapshot.messages;
        delete nextSnapshot.timeline;
        projection.snapshot = nextSnapshot;
    }
    return projection;
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const startedAt = Date.now();
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;
    const publicBaseUrl = resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: false });
    const omitMessages = req.nextUrl.searchParams.get("omitMessages") === "1";

    try {
        const [snapshotResponse, historyResponse] = await Promise.all([
            fetch(`${ENGINE_URL}/sessions/${id}/snapshot${omitMessages ? "?compact=1" : ""}`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
            }),
            omitMessages ? Promise.resolve(null) : fetch(`${ENGINE_URL}/sessions/${id}/history`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
            }),
        ]);

        if (!snapshotResponse.ok) {
            console.error("[Client Conversations] Failed to fetch session snapshot:", await snapshotResponse.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: 500 });
        }

        const snapshotData = normalizeSnapshotForRealtimeSurface(
            await snapshotResponse.json().catch(() => ({})),
            { publicBaseUrl, compactSurface: omitMessages },
        ) as Record<string, unknown>;
        const projectionData = omitMessages ? stripMessagesForProjection(snapshotData) : snapshotData;
        const historyData = historyResponse && historyResponse.ok
            ? await historyResponse.json().catch(() => ({}))
            : null;
        const historyRecord = asRecord(historyData);
        const snapshotMessages = omitMessages
            ? []
            : (
                Array.isArray(asRecord(snapshotData.snapshot).messages)
                    ? asRecord(snapshotData.snapshot).messages
                    : Array.isArray(snapshotData.messages)
                        ? snapshotData.messages
                        : []
            );
        const historyTimeline = Array.isArray(historyRecord.timeline)
            ? historyRecord.timeline.map((message: unknown) => normalizeMessageForRealtimeSurface(message, { publicBaseUrl }))
            : [];
        const historyMessages = Array.isArray(historyRecord.messages)
            ? historyRecord.messages.map((message: unknown) => normalizeMessageForRealtimeSurface(message, { publicBaseUrl }))
            : [];
        const detailMessages = omitMessages
            ? []
            : (
                historyTimeline.length > 0
                    ? historyTimeline
                    : historyMessages.length > 0
                        ? historyMessages
                        : snapshotMessages
            );
        const historyProcesses = Array.isArray(historyRecord.processes)
            ? historyRecord.processes
                .map((process: unknown) => normalizeProcessForRealtimeSurface(process))
                .filter((process): process is NonNullable<ReturnType<typeof normalizeProcessForRealtimeSurface>> => Boolean(process))
            : [];
        const snapshotProcesses = Array.isArray(snapshotData.processes) ? snapshotData.processes : [];

        const engineNow = snapshotResponse.headers.get(ENGINE_NOW_HEADER) || historyResponse?.headers.get(ENGINE_NOW_HEADER);

        const responsePayload = applyCanonicalSourceGroup({
            id,
            messages: detailMessages,
            timeline: historyTimeline.length > 0 ? historyTimeline : historyMessages,
            ledger: Array.isArray(historyRecord.ledger) ? historyRecord.ledger : [],
            latestSeq: snapshotData.latestSeq || 0,
            source: snapshotData.source || "runtime_snapshot",
            todos: snapshotData.todos || null,
            currentRun: snapshotData.currentRun || null,
            runtimeStatus: snapshotData.runtimeStatus || null,
            workflow: snapshotData.workflow || null,
            workflowProjection: snapshotData.workflowProjection || null,
            approvals: Array.isArray(snapshotData.approvals) ? snapshotData.approvals : [],
            askUserInteractions: Array.isArray(historyRecord.askUserInteractions)
                ? historyRecord.askUserInteractions
                : Array.isArray(snapshotData.askUserInteractions)
                    ? snapshotData.askUserInteractions
                    : [],
            controls: snapshotData.controls || null,
            recoverable: snapshotData.recoverable || null,
            summary: snapshotData.summary || null,
            processes: historyProcesses.length > 0 ? historyProcesses : snapshotProcesses,
            runtimeTimeline: Array.isArray(historyRecord.runtimeTimeline) ? historyRecord.runtimeTimeline : snapshotData.runtimeTimeline || [],
            contextReferences: Array.isArray(historyRecord.contextReferences) ? historyRecord.contextReferences : snapshotData.contextReferences || [],
            artifacts: Array.isArray(historyRecord.artifacts) ? historyRecord.artifacts : snapshotData.artifacts || [],
            contextGovernance: Object.keys(asRecord(historyRecord.contextGovernance)).length > 0
                ? historyRecord.contextGovernance
                : snapshotData.contextGovernance || null,
            contextGovernanceHistory: Array.isArray(historyRecord.contextGovernanceHistory)
                ? historyRecord.contextGovernanceHistory
                : Array.isArray(snapshotData.contextGovernanceHistory)
                    ? snapshotData.contextGovernanceHistory
                    : [],
            sessionCoordinationMessages: Array.isArray(historyRecord.sessionCoordinationMessages)
                ? historyRecord.sessionCoordinationMessages
                : Array.isArray(snapshotData.sessionCoordinationMessages)
                    ? snapshotData.sessionCoordinationMessages
                    : [],
            projection: projectionData,
            _profile: {
                snapshot: snapshotData._profile,
                history: historyRecord._profile,
            },
        });
        const elapsedMs = Date.now() - startedAt;
        const payloadBytes = jsonSizeBytes(responsePayload);
        recordAdminApiMetric({
            route: "client.conversations.detail",
            status: 200,
            elapsedMs,
            payloadBytes,
            engineElapsedMs: Math.max(readEngineElapsedMs(snapshotData) || 0, readEngineElapsedMs(historyRecord) || 0),
        });

        return NextResponse.json(responsePayload, {
            headers: {
                ...(engineNow ? { [ENGINE_NOW_HEADER]: engineNow } : {}),
                "x-v8-admin-proxy-ms": String(elapsedMs),
                "x-v8-payload-bytes": String(payloadBytes),
            },
        });
    } catch (error) {
        console.error("[Client Conversations] Engine communication failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;

    try {
        const response = await fetch(`${ENGINE_URL}/sessions/${id}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
        });

        if (!response.ok) {
            console.error("[Client Conversations] Failed to delete session:", await response.text());
            return NextResponse.json({ error: "Failed to delete" }, { status: 500 });
        }

        return NextResponse.json({ success: true });
    } catch (error) {
        console.error("[Client Conversations] Delete session failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
