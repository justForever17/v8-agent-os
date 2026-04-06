import { NextRequest, NextResponse } from "next/server";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { normalizeSnapshotForRealtimeSurface } from "@/lib/server/session-realtime-resource";
import { applyCanonicalSourceGroup } from "@/lib/server/source-group";

const ENGINE_URL = resolveEngineBaseUrl();

function asRecord(value: unknown) {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    const { id } = await params;

    try {
        const snapshotRes = await fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            cache: 'no-store'
        });

        if (!snapshotRes.ok) {
            console.error("Failed to fetch session snapshot from Python engine:", await snapshotRes.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: 500 });
        }

        const snapshotData = normalizeSnapshotForRealtimeSurface(await snapshotRes.json().catch(() => ({}))) as Record<string, unknown>;
        const snapshotMessages = Array.isArray(asRecord(snapshotData.snapshot).messages)
            ? asRecord(snapshotData.snapshot).messages
            : Array.isArray(snapshotData.messages)
                ? snapshotData.messages
                : [];

        return NextResponse.json(applyCanonicalSourceGroup({
            id,
            messages: snapshotMessages,
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
            projection: snapshotData,
        }));
    } catch (error) {
        console.error("Error communicating with Python engine:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const userEmail = await resolveAuthorizedUserEmail(req);

    if (!userEmail) {
        return unauthorizedJson();
    }

    const { id } = await params;

    try {
        const res = await fetch(`${ENGINE_URL}/sessions/${id}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!res.ok) {
            console.error("Failed to delete session in Python engine:", await res.text());
            return NextResponse.json({ error: "Failed to delete" }, { status: 500 });
        }

        return NextResponse.json({ success: true });
    } catch (error) {
        console.error("Error communicating with Python engine:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
