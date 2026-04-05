import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { applyCanonicalSourceGroup } from "@/lib/server/source-group";

const ENGINE_URL = resolveEngineBaseUrl();

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
        const snapshotResponse = await fetch(`${ENGINE_URL}/sessions/${id}/snapshot`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (snapshotResponse.ok) {
            const snapshotData = await snapshotResponse.json().catch(() => ({}));
            const snapshotMessages = snapshotData?.snapshot?.messages;
            if (Array.isArray(snapshotMessages)) {
                return NextResponse.json(applyCanonicalSourceGroup({
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
                }));
            }
        }

        const response = await fetch(`${ENGINE_URL}/sessions/${id}/messages`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });

        if (!response.ok) {
            console.error("[Client Conversations] Failed to fetch session messages:", await response.text());
            return NextResponse.json({ error: "Failed to fetch from engine" }, { status: 500 });
        }

        const data = await response.json();
        const messages = data.messages || [];

        return NextResponse.json(applyCanonicalSourceGroup({
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
