import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { normalizeMessageForRealtimeSurface, normalizeProcessForRealtimeSurface, normalizeSnapshotForRealtimeSurface } from "@/lib/server/session-realtime-resource";
import { applyCanonicalSourceGroup } from "@/lib/server/source-group";

const ENGINE_URL = resolveEngineBaseUrl();

function asRecord(value: unknown) {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;

    try {
        const [snapshotResponse, historyResponse] = await Promise.all([
            fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
            }),
            fetch(`${ENGINE_URL}/sessions/${id}/history`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
                cache: "no-store",
            }),
        ]);

        if (!snapshotResponse.ok) {
            console.error("[Client Conversations] Failed to fetch session snapshot:", await snapshotResponse.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: 500 });
        }

        const snapshotData = normalizeSnapshotForRealtimeSurface(await snapshotResponse.json().catch(() => ({}))) as Record<string, unknown>;
        const historyData = historyResponse.ok
            ? await historyResponse.json().catch(() => ({}))
            : null;
        const historyRecord = asRecord(historyData);
        const snapshotMessages = Array.isArray(asRecord(snapshotData.snapshot).messages)
            ? asRecord(snapshotData.snapshot).messages
            : Array.isArray(snapshotData.messages)
                ? snapshotData.messages
                : [];
        const historyTimeline = Array.isArray(historyRecord.timeline)
            ? historyRecord.timeline.map((message: unknown) => normalizeMessageForRealtimeSurface(message))
            : [];
        const historyMessages = Array.isArray(historyRecord.messages)
            ? historyRecord.messages.map((message: unknown) => normalizeMessageForRealtimeSurface(message))
            : [];
        const detailMessages = historyTimeline.length > 0
            ? historyTimeline
            : historyMessages.length > 0
                ? historyMessages
                : snapshotMessages;
        const historyProcesses = Array.isArray(historyRecord.processes)
            ? historyRecord.processes
                .map((process: unknown) => normalizeProcessForRealtimeSurface(process))
                .filter((process): process is NonNullable<ReturnType<typeof normalizeProcessForRealtimeSurface>> => Boolean(process))
            : [];
        const snapshotProcesses = Array.isArray(snapshotData.processes) ? snapshotData.processes : [];

        return NextResponse.json(applyCanonicalSourceGroup({
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
            projection: snapshotData,
        }));
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
