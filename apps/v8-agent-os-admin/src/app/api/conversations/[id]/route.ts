import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    let userEmail: string | undefined | null;
    userEmail = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }

    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id } = await params;

    try {
        const snapshotRes = await fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            cache: 'no-store'
        });

        if (snapshotRes.ok) {
            const snapshotData = await snapshotRes.json().catch(() => ({}));
            const snapshotMessages = snapshotData?.snapshot?.messages;
            if (Array.isArray(snapshotMessages)) {
                return NextResponse.json({
                    id,
                    messages: snapshotMessages,
                    latestSeq: snapshotData.latestSeq || 0,
                    source: snapshotData.source || "runtime_snapshot",
                    workflow: snapshotData.workflow || null,
                    workflowProjection: snapshotData.workflowProjection || null,
                    approvals: Array.isArray(snapshotData.approvals) ? snapshotData.approvals : [],
                    controls: snapshotData.controls || null,
                    recoverable: snapshotData.recoverable || null,
                    summary: snapshotData.summary || null,
                    projection: snapshotData,
                });
            }
        }

        // Fallback to durable detail projection if runtime snapshot is unavailable.
        const res = await fetch(`${ENGINE_URL}/sessions/${id}/messages`, {
            method: 'GET',
            headers: { 'Content-Type': 'application/json' },
            cache: 'no-store'
        });

        if (!res.ok) {
            console.error("Failed to fetch session messages from Python engine:", await res.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: 500 });
        }

        const data = await res.json();
        const messages = data.messages || [];

        return NextResponse.json({
            id,
            messages,
            latestSeq: data.latestSeq || 0,
            source: data.source || "durable_detail_projection",
            workflow: data.workflow || null,
            workflowProjection: data.workflowProjection || null,
            approvals: Array.isArray(data.approvals) ? data.approvals : [],
            controls: data.controls || null,
            recoverable: data.recoverable || null,
            summary: data.summary || null,
        });
    } catch (error) {
        console.error("Error communicating with Python engine:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    let userEmail: string | undefined | null;
    userEmail = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }

    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
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
